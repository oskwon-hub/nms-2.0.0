import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 17장: 개발 시 Vite dev server에서 API 요청을 백엔드로 프록시한다.
// 배포 시(scripts/deploy.sh)에는 빌드 결과물이 FastAPI 정적 파일로 직접 서빙되므로
// 프록시가 필요 없다.
//
// 이 서버는 다수의 다른 서비스(nms-1.3.0의 8000/5173 포함)가 이미 넓은 포트
// 대역을 점유하고 있어, 충돌 가능성이 낮은 18090/15180을 기본값으로 사용한다.
// 필요 시 환경변수로 재정의한다.
const FRONTEND_PORT = Number(process.env.NMS_FRONTEND_PORT ?? 15180);
const BACKEND_PORT = Number(process.env.NMS_BACKEND_PORT ?? 18090);

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0", // 이 서버의 다른 프로젝트들과 동일하게 외부(원격 브라우저)에서 접속 가능해야 함
    port: FRONTEND_PORT,
    strictPort: true,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${BACKEND_PORT}`,
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: "0.0.0.0",
    port: FRONTEND_PORT,
    strictPort: true,
  },
});
