// [KOS20260922] 백엔드 SQLite는 시각을 UTC로 저장하지만 API에는 시간대 표시가
// 없는 문자열로 나온다. 브라우저가 이를 현지 시각으로 해석하면 한국에서 9시간 늦게 보인다.
const kstFormatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

export function parseUtcDateTime(value: string): Date {
  const iso = value.trim().replace(" ", "T");
  return new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso : `${iso}Z`);
}

export function formatUtcDateTime(value: string | null): string {
  if (!value) return "-";
  const date = parseUtcDateTime(value);
  if (Number.isNaN(date.getTime())) return "-";
  const parts = Object.fromEntries(kstFormatter.formatToParts(date).map(({ type, value: part }) => [type, part]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} KST`;
}
