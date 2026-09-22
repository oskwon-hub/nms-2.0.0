// [KOS20260922] Settings 오른쪽에 Help 메뉴를 추가해 달라는 요청 - 제품 정보
// (회사/버전/작성일)와 라이선스(GPLv2)를 확인할 수 있는 About 화면을 제공한다.
const ABOUT = {
  productName: "NMS (Network Management System)",
  company: "엔에스티정보통신 (NST Information & Communications Co., Ltd.)",
  version: "2.0.0",
  writtenAt: "2026-09-20",
  license: "GNU General Public License v2.0 (GPLv2)",
};

export default function HelpPage() {
  return (
    <div>
      <div className="page-header">
        <h1>Help</h1>
      </div>

      <div className="card">
        <div className="tree-group-title">About</div>
        <table>
          <tbody>
            <tr>
              <th>제품명</th>
              <td>{ABOUT.productName}</td>
            </tr>
            <tr>
              <th>제작사</th>
              <td>{ABOUT.company}</td>
            </tr>
            <tr>
              <th>버전</th>
              <td>{ABOUT.version}</td>
            </tr>
            <tr>
              <th>작성일</th>
              <td>{ABOUT.writtenAt}</td>
            </tr>
            <tr>
              <th>라이선스</th>
              <td>{ABOUT.license}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="tree-group-title">라이선스 (License)</div>
        <p className="muted" style={{ fontSize: 13, lineHeight: 1.6 }}>
          이 프로그램은 자유 소프트웨어입니다. 당신은 자유 소프트웨어 재단(Free Software Foundation)이 발표한 GNU
          General Public License 버전 2(GPLv2) 조항에 따라 이 프로그램을 재배포하거나 수정할 수 있습니다.
        </p>
        <p className="muted" style={{ fontSize: 13, lineHeight: 1.6 }}>
          이 프로그램은 유용하게 사용될 수 있기를 바라는 마음에서 배포되지만, 어떠한 형태의 보증도 제공하지
          않습니다. 상품성이나 특정 목적에 대한 적합성에 대한 묵시적 보증조차 하지 않습니다. 자세한 사항은
          GNU General Public License를 참고하십시오.
        </p>
        <p className="muted" style={{ fontSize: 13 }}>
          전체 라이선스 원문은 프로젝트 루트의 <code>LICENSE</code> 파일을 참고하세요.
        </p>
      </div>
    </div>
  );
}
