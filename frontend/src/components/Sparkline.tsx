// 近期走勢迷你折線（純 SVG，無外部依賴）。台股配色：紅漲綠跌，依序列首尾決定。
export function Sparkline({
  data,
  width = 320,
  height = 64,
}: {
  data: number[] | null | undefined;
  width?: number;
  height?: number;
}) {
  if (!data || data.length < 2) {
    return <div className="h-16 text-xs text-muted">近期走勢資料不足</div>;
  }

  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const pad = 2;
  const stepX = (width - pad * 2) / (data.length - 1);
  const y = (v: number) => pad + (1 - (v - min) / span) * (height - pad * 2);
  const points = data.map((v, i) => `${pad + i * stepX},${y(v)}`).join(" ");

  const up = data[data.length - 1] >= data[0];
  const stroke = up ? "#e11d48" : "#16a34a"; // 紅漲 / 綠跌
  const fill = up ? "#e11d4822" : "#16a34a22";
  const areaPoints = `${pad},${height - pad} ${points} ${pad + (data.length - 1) * stepX},${height - pad}`;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="h-16 w-full"
    >
      <polygon points={areaPoints} fill={fill} />
      <polyline
        points={points}
        fill="none"
        stroke={stroke}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
