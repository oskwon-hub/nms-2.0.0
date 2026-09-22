import { useEffect } from "react";
import { createPortal } from "react-dom";

interface MenuItem {
  label: string;
  danger?: boolean;
  onClick: () => void;
}

interface TopologyContextMenuProps {
  x: number;
  y: number;
  items: MenuItem[];
  onClose: () => void;
}

export default function TopologyContextMenu({ x, y, items, onClose }: TopologyContextMenuProps) {
  useEffect(() => {
    const close = () => onClose();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [onClose]);

  return createPortal(
    <div
      role="menu"
      style={{
        position: "fixed",
        left: Math.min(x, window.innerWidth - 180),
        top: Math.min(y, window.innerHeight - 60),
        zIndex: 3000,
        minWidth: 170,
        padding: 4,
        border: "1px solid #d1d5db",
        borderRadius: 6,
        background: "white",
        boxShadow: "0 8px 24px rgba(0, 0, 0, 0.18)",
      }}
      onMouseDown={(event) => event.stopPropagation()}
      onContextMenu={(event) => event.preventDefault()}
    >
      {items.map((item) => (
        <button
          key={item.label}
          type="button"
          role="menuitem"
          onClick={() => {
            item.onClick();
            onClose();
          }}
          style={{
            display: "block",
            width: "100%",
            padding: "8px 10px",
            border: 0,
            borderRadius: 4,
            background: "transparent",
            color: item.danger ? "#dc2626" : "#111827",
            textAlign: "left",
            cursor: "pointer",
          }}
        >
          {item.label}
        </button>
      ))}
    </div>,
    document.body,
  );
}
