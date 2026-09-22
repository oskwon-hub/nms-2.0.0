"""저장된 장비 관리 IP에 대한 서버 측 네트워크 진단."""
from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
import telnetlib
import time
from pathlib import Path

import paramiko


_PERMISSION_ERRORS = ("operation not permitted", "permission denied", "not enough privileges")
_SYS_CLASS_NET = Path("/sys/class/net")
_HOP_LINES = {
    "tracepath": re.compile(r"^\s*(\d+)\??:\s+(.+)$"),
    "mtr": re.compile(r"^\s*(\d+)\.\|--\s+(.+)$"),
    "traceroute": re.compile(r"^\s*(\d+)\s+(.+)$"),
}
_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def _clean_cli_output(output: str) -> str:
    """터미널 화면 제어 문자를 제거해 UI에서 읽을 수 있는 CLI 출력으로 만든다."""
    return _ANSI_ESCAPE_RE.sub("", output).replace("\x00", "").strip()


def parse_ping_metrics(output: str) -> tuple[bool, float | None, float | None, float | None, float | None]:
    """Linux/Windows 및 주요 네트워크 장비 Ping 출력에서 성공률과 RTT를 추출한다."""
    loss: float | None = None
    loss_match = re.search(r"([0-9]+(?:\.[0-9]+)?)%\s*(?:packet\s+)?loss", output, re.IGNORECASE)
    if loss_match:
        loss = float(loss_match.group(1))
    success_match = re.search(r"Success +rate +is +([0-9]+) +percent", output, re.IGNORECASE)
    if success_match:
        loss = 100.0 - float(success_match.group(1))

    rtt_match = re.search(
        r"(?:min/avg/max(?:/[^=\s]+)?|round-trip min/avg/max)\s*=\s*"
        r"([0-9.]+)/([0-9.]+)/([0-9.]+)",
        output,
        re.IGNORECASE,
    )
    rtt_min = float(rtt_match.group(1)) if rtt_match else None
    rtt_avg = float(rtt_match.group(2)) if rtt_match else None
    rtt_max = float(rtt_match.group(3)) if rtt_match else None
    success = (loss is not None and loss < 100) or "!!!!!" in output
    return success, loss, rtt_min, rtt_avg, rtt_max


def run_remote_ping(
    source_host: str,
    target_ip: str,
    username: str,
    password: str,
    port: int,
    source_description: str = "",
    protocol: str = "SSH",
) -> tuple[str, bool, str, float | None, float | None, float | None, float | None]:
    """From 장비에 SSH 또는 Telnet으로 접속해 CLI에서 To IP로 Ping을 실행한다."""
    source = str(ipaddress.ip_address(source_host))
    target = str(ipaddress.ip_address(target_ip))
    description = source_description.lower()
    if "windows" in description:
        command = f"ping -n 4 -w 2000 {target}"
    elif any(token in description for token in ("linux", "ubuntu", "debian", "unix", "macos")):
        command = f"ping -c 4 -W 2 {target}"
    else:
        # Cisco/HPE/Aruba 등 네트워크 장비 CLI에서 공통으로 지원되는 기본형.
        command = f"ping {target}"

    protocol = protocol.upper()
    if protocol == "TELNET":
        try:
            with telnetlib.Telnet(source, port, timeout=8) as client:
                login_patterns = [br"(?i)(?:user ?name|login)\s*:\s*$"]
                index, _, banner = client.expect(login_patterns, timeout=5)
                if index < 0:
                    raise OSError("사용자명 입력 Prompt를 찾지 못했습니다.")
                client.write(username.encode() + b"\n")
                index, _, password_prompt = client.expect([br"(?i)password\s*:\s*$"], timeout=5)
                if index < 0:
                    raise OSError("암호 입력 Prompt를 찾지 못했습니다.")
                client.write(password.encode() + b"\n")
                index, _, login_output = client.expect([br"(?m)[^\r\n]+[>#]\s*$"], timeout=8)
                if index < 0:
                    raise OSError("로그인 후 CLI Prompt를 찾지 못했습니다.")
                client.write(command.encode() + b"\n")
                index, _, command_output = client.expect([br"(?m)[^\r\n]+[>#]\s*$"], timeout=15)
                output = _clean_cli_output(
                    (banner + password_prompt + login_output + command_output).decode(errors="replace")
                )[:16000]
                if index < 0:
                    output = (output + "\nTelnet Ping 응답 제한 시간(15초)을 초과했습니다.").strip()
        except (EOFError, OSError, ValueError) as exc:
            return command, False, f"TELNET 접속/실행 실패 {source}: {exc}", None, None, None, None
        success, loss, rtt_min, rtt_avg, rtt_max = parse_ping_metrics(output)
        return command, success, output or "Ping 명령이 출력을 반환하지 않았습니다.", loss, rtt_min, rtt_avg, rtt_max

    if protocol != "SSH":
        return command, False, f"지원하지 않는 CLI 프로토콜: {protocol}", None, None, None, None

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            source,
            port=port,
            username=username,
            password=password,
            timeout=8,
            auth_timeout=8,
            banner_timeout=8,
            look_for_keys=False,
            allow_agent=False,
        )
        shell = client.invoke_shell(term="vt100", width=200, height=50)
        shell.settimeout(0.3)
        # 일부 Dropbear 기반 스위치는 Shell을 연 뒤 입력 없이 기다리면 채널을
        # 닫는다. 초기 배너를 오래 기다리지 않고 즉시 명령을 보낸다.
        shell.send(command + "\n")
        deadline = time.monotonic() + 15
        last_data_at = time.monotonic()
        received = False
        chunks: list[bytes] = []
        while time.monotonic() < deadline:
            if shell.recv_ready():
                chunk = shell.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                received = True
                last_data_at = time.monotonic()
                continue
            if received and time.monotonic() - last_data_at >= 1.8:
                break
            if shell.closed:
                break
            time.sleep(0.1)
        output = _clean_cli_output(b"".join(chunks).decode(errors="replace"))[:16000]
    except (paramiko.SSHException, OSError, ValueError) as exc:
        return command, False, f"SSH 접속/실행 실패 {source}: {exc}", None, None, None, None
    finally:
        client.close()

    success, loss, rtt_min, rtt_avg, rtt_max = parse_ping_metrics(output)
    return command, success, output or "Ping 명령이 출력을 반환하지 않았습니다.", loss, rtt_min, rtt_avg, rtt_max


def get_source_ip(target: str) -> str | None:
    """목적지별 라우팅 선택에 따라 NMS 서버가 사용할 출발지 IP를 조회한다."""
    address = ipaddress.ip_address(target)
    try:
        proc = subprocess.run(
            ["ip", "-6" if address.version == 6 else "-4", "route", "get", str(address)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    match = re.search(r"(?:^|\s)src\s+(\S+)", proc.stdout)
    if not match:
        return None
    try:
        return str(ipaddress.ip_address(match.group(1)))
    except ValueError:
        return None


def get_source_mac(target: str) -> str | None:
    """목적지 경로에 선택된 로컬 인터페이스의 MAC 주소를 읽는다."""
    address = ipaddress.ip_address(target)
    try:
        proc = subprocess.run(
            ["ip", "-6" if address.version == 6 else "-4", "route", "get", str(address)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    match = re.search(r"(?:^|\s)dev\s+([A-Za-z0-9][A-Za-z0-9_.:-]*)(?:\s|$)", proc.stdout)
    if not match:
        return None
    try:
        mac = (_SYS_CLASS_NET / match.group(1) / "address").read_text().strip().lower()
    except OSError:
        return None
    return mac if re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", mac) else None


def parse_route_hops(command: str, output: str) -> list[tuple[int, str | None]]:
    """tracepath/mtr/traceroute 출력에서 순서와 응답 IP를 추출한다."""
    pattern = _HOP_LINES.get(command)
    if pattern is None:
        return []
    hops: list[tuple[int, str | None]] = []
    seen: set[tuple[int, str | None]] = set()
    for line in output.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        if command == "tracepath" and "[LOCALHOST]" in match.group(2):
            # [KOS20260922] "1?: [LOCALHOST] pmtu ..."는 경유 장비가 아니라
            # 로컬 MTU 정보다. 같은 홉의 실제 응답이 뒤에 이어질 수 있다.
            continue
        address = None
        for token in match.group(2).split():
            candidate = token.strip("[](),").split("%", 1)[0]
            try:
                address = str(ipaddress.ip_address(candidate))
                break
            except ValueError:
                continue
        hop = (int(match.group(1)), address)
        if hop not in seen:
            hops.append(hop)
            seen.add(hop)
    # [KOS20260922] 동일한 홉에 "no reply"와 실제 IP가 모두 나타나면
    # 응답한 IP를 우선한다. 서로 다른 IP는 경로가 여러 개인 경우라 유지한다.
    responsive = {number for number, address in hops if address is not None}
    return [(number, address) for number, address in hops if address is not None or number not in responsive]


def _route_commands(target: str, protocol: str) -> list[list[str]]:
    commands: list[list[str]] = []
    # [KOS20260922] tracepath는 일반 사용자 UDP 경로 추적에 적합하다. mtr은
    # cap_net_raw가 설정된 mtr-packet을 통해 TCP/ICMP도 실행할 수 있다.
    if protocol == "UDP" and shutil.which("tracepath"):
        commands.append(["tracepath", "-n", "-m", "20", target])
    if shutil.which("mtr"):
        method = {"TCP": ["--tcp"], "UDP": ["--udp"], "ICMP": []}[protocol]
        commands.append(["mtr", "-n", "-r", "-c", "3", "-m", "20", *method, target])
    if shutil.which("traceroute"):
        method = {"TCP": "-T", "UDP": "-U", "ICMP": "-I"}[protocol]
        commands.append(["traceroute", method, "-n", "-m", "20", "-w", "1", "-q", "1", target])
    return commands


def run_diagnostic(ip: str, kind: str, protocol: str | None = None) -> tuple[str, bool, str]:
    # [KOS20260922] 인벤토리 값이 명령 옵션이나 호스트명으로 해석되지 않도록 IP만 허용한다.
    target = str(ipaddress.ip_address(ip))
    if kind == "ping":
        if protocol not in (None, "ICMP"):
            raise ValueError("Ping test는 ICMP만 지원합니다.")
        commands = [["ping", "-n", "-c", "4", "-W", "2", target]]
        timeout = 12
    elif kind == "traceroute":
        protocol = protocol or "UDP"
        if protocol not in {"TCP", "UDP", "ICMP"}:
            raise ValueError(f"지원하지 않는 경로 추적 프로토콜: {protocol}")
        commands = _route_commands(target, protocol)
        if not commands:
            return "traceroute", False, f"{protocol} 경로 추적에 필요한 명령(tracepath, mtr, traceroute)을 찾을 수 없습니다."
        timeout = 30
    else:
        raise ValueError(f"지원하지 않는 진단: {kind}")

    deadline = time.monotonic() + timeout
    permission_errors = []
    for command in commands:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            proc = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout or b""
            if isinstance(partial, bytes):
                partial = partial.decode(errors="replace")
            return command[0], False, f"{partial[:16000]}\n제한 시간({timeout}초)을 초과했습니다.".strip()
        except (FileNotFoundError, OSError) as exc:
            permission_errors.append(f"{command[0]}: {exc}")
            continue

        output = (proc.stdout + proc.stderr).strip()[:16000]
        if proc.returncode != 0 and any(message in output.lower() for message in _PERMISSION_ERRORS):
            permission_errors.append(f"{command[0]}: {output}")
            continue
        return command[0], proc.returncode == 0, output or f"명령이 종료 코드 {proc.returncode}로 끝났습니다."

    if kind == "ping":
        guidance = "Ping 소켓 권한이 없습니다. NMS 서비스의 네트워크 권한을 확인해 주세요."
    else:
        guidance = (
            "경로 추적에 필요한 소켓 권한이 없습니다. NMS 서버의 mtr-packet 또는 traceroute에 "
            "CAP_NET_RAW 권한을 설정하고 서비스의 권한 제한을 확인해 주세요."
        )
    return commands[-1][0], False, (guidance + "\n" + "\n".join(permission_errors))[:16000]
