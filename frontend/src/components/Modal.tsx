import { useEffect } from "react";
import { createPortal } from "react-dom";

interface ModalProps {
  onClose: () => void;
  children: React.ReactNode;
  width?: number;
}

// [KOS20260921] Topology에서 "상세 보기"를 누르면 메인 화면(Devices 라우트)으로
// 전환돼 버려 그래프 컨텍스트를 잃는다는 지적에 따라, 페이지 이동 대신 팝업으로
// 같은 상세 화면을 띄운다. 배경 클릭/Esc로 닫을 수 있다.
export default function Modal({ onClose, children, width = 900 }: ModalProps) {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return createPortal(
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-content" style={{ width }} onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>,
    document.body,
  );
}
