import { Handle, NodeProps, Position } from "reactflow";

export interface RoleCloudNodeData {
  label: string;
  color: string;
  count: number;
  onlineCount: number;
}

// [KOS20260922] "구름으로 표시" 요청을 문자 그대로 구현한다 - 사각형 대신 실제
// 네트워크 다이어그램에서 흔히 쓰는 구름 모양(SVG Path)으로 Role 그룹을 그린다.
export default function RoleCloudNode({ data }: NodeProps<RoleCloudNodeData>) {
  return (
    <div style={{ position: "relative", width: 160, height: 100 }}>
      <Handle type="target" position={Position.Top} style={{ opacity: 0 }} />
      <svg viewBox="0 0 200 130" width="160" height="100" style={{ position: "absolute", inset: 0 }}>
        <path
          d="M50 100 C22 100 2 82 2 58 C2 34 22 18 46 18 C52 -4 84 -8 102 6 C112 -10 148 -8 158 12 C184 14 198 32 198 56 C198 80 180 100 156 100 Z"
          fill={data.color}
          stroke="rgba(0,0,0,0.25)"
          strokeWidth={2}
        />
      </svg>
      <div
        style={{
          position: "relative",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          color: "white",
          textAlign: "center",
          pointerEvents: "none",
        }}
      >
        <div style={{ fontSize: 13, fontWeight: 700 }}>{data.label}</div>
        <div style={{ fontSize: 11, fontWeight: 400 }}>
          {data.count}대 (온라인 {data.onlineCount})
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
    </div>
  );
}
