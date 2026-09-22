import { useNavigate, useParams } from "react-router-dom";
import DeviceDetailContent from "../components/DeviceDetailContent";

export default function DeviceDetailPage() {
  const { id } = useParams();
  const deviceId = Number(id);
  const navigate = useNavigate();

  return (
    <DeviceDetailContent
      deviceId={deviceId}
      headerExtra={
        <button className="btn" onClick={() => navigate(-1)}>
          ← 뒤로
        </button>
      }
    />
  );
}
