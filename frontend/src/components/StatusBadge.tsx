// 17.3절: Normal(녹색)/Warning(황색)/Critical-Down(적색)/Offline(회색)/Unknown(보라색) 배지.
const STATUS_CLASS: Record<string, string> = {
  ONLINE: "badge-online",
  STALE: "badge-stale",
  OFFLINE: "badge-offline",
  UNKNOWN: "badge-unknown",
  RUNNING: "badge-running",
  COMPLETED: "badge-online",
  FAILED: "badge-offline",
  CANCELLED: "badge-stale",
  UP: "badge-online",
  DOWN: "badge-offline",
};

export default function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_CLASS[status] ?? "badge-unknown";
  return <span className={`badge ${cls}`}>{status}</span>;
}
