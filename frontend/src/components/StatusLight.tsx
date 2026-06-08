// 出場狀態燈（持股）/ 進場狀態燈（觀察，P6）共用
const LABEL: Record<string, string> = {
  red: "建議出場",
  orange: "警戒",
  yellow: "留意",
  green: "續抱",
};

export function StatusLight({ light, level }: { light: string; level: string }) {
  return (
    <span className="inline-flex items-center gap-1" title={LABEL[level] ?? ""}>
      <span className="text-base leading-none">{light}</span>
    </span>
  );
}

export { LABEL as STATUS_LABEL };
