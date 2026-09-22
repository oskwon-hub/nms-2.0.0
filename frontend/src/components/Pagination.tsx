import { useState } from "react";

// 목록 화면(Discovery/Alarms/Reports/Settings) 공용 페이지 네비게이션.
// 서버는 limit만 지원하고 offset/cursor 페이지네이션이 없으므로, 호출부에서
// 넉넉한 limit(예: 500)으로 전체 목록을 받아온 뒤 이 컴포넌트가 순수 프론트엔드
// 슬라이싱으로 페이지를 나눈다 (백엔드 변경 없음).
export interface PaginationProps {
  /** 1-indexed 현재 페이지 */
  page: number;
  pageSize: number;
  totalItems: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  pageSizeOptions?: number[];
}

const DEFAULT_PAGE_SIZE_OPTIONS = [20, 50, 100, 200, 500];
const PAGE_WINDOW = 2;

export default function Pagination({
  page,
  pageSize,
  totalItems,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = DEFAULT_PAGE_SIZE_OPTIONS,
}: PaginationProps) {
  const [jumpValue, setJumpValue] = useState("");

  if (totalItems <= 0) return null;

  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const current = Math.min(Math.max(page, 1), totalPages);

  const goTo = (target: number) => {
    const clamped = Math.min(Math.max(target, 1), totalPages);
    if (clamped !== current) onPageChange(clamped);
  };

  const handleJump = () => {
    const n = parseInt(jumpValue, 10);
    if (!Number.isNaN(n) && n >= 1 && n <= totalPages) {
      goTo(n);
    }
    setJumpValue("");
  };

  const pages: Array<number | "ellipsis"> = [];
  const start = Math.max(1, current - PAGE_WINDOW);
  const end = Math.min(totalPages, current + PAGE_WINDOW);

  if (start > 1) {
    pages.push(1);
    if (start > 2) pages.push("ellipsis");
  }
  for (let p = start; p <= end; p++) pages.push(p);
  if (end < totalPages) {
    if (end < totalPages - 1) pages.push("ellipsis");
    pages.push(totalPages);
  }

  return (
    <div className="pagination">
      <select
        value={pageSize}
        onChange={(e) => onPageSizeChange(Number(e.target.value))}
        aria-label="페이지당 개수"
      >
        {pageSizeOptions.map((size) => (
          <option key={size} value={size}>
            페이지당 {size}개
          </option>
        ))}
      </select>

      <button className="btn" disabled={current <= 1} onClick={() => goTo(1)}>
        맨처음
      </button>
      <button className="btn" disabled={current <= 1} onClick={() => goTo(current - 1)}>
        이전
      </button>

      {pages.map((p, idx) =>
        p === "ellipsis" ? (
          <span key={`ellipsis-${idx}`} className="pagination-ellipsis">
            ...
          </span>
        ) : (
          <button
            key={p}
            className={`btn${p === current ? " btn-primary" : ""}`}
            onClick={() => goTo(p)}
            disabled={p === current}
          >
            {p}
          </button>
        ),
      )}

      <button className="btn" disabled={current >= totalPages} onClick={() => goTo(current + 1)}>
        다음
      </button>
      <button className="btn" disabled={current >= totalPages} onClick={() => goTo(totalPages)}>
        맨끝
      </button>

      <span className="pagination-jump">
        <input
          type="text"
          inputMode="numeric"
          value={jumpValue}
          placeholder={`${current}`}
          onChange={(e) => setJumpValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleJump();
          }}
          style={{ width: 56 }}
          aria-label="이동할 페이지 번호"
        />
        <button className="btn" onClick={handleJump}>
          이동
        </button>
      </span>
    </div>
  );
}
