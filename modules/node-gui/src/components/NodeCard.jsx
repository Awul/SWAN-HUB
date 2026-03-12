import SensorItem from "./SensorItem";
import toast from "react-hot-toast";
import { useEffect, useRef } from "react";

export default function NodeCard({ nodeId, node }) {
  const lastSeen = node.last_seen ?? 0;

  // lighter background colors
  let bgColor = "bg-gray-800"; // default dark gray
  if (lastSeen >= 25) bgColor = "bg-red-800/40";       // offline
  else if (lastSeen >= 15) bgColor = "bg-yellow-800/30"; // warning
  else bgColor = "bg-green-800/30";                    // healthy

  // keep track if we already notified
  const notifiedRef = useRef(false);

  useEffect(() => {
    if (lastSeen >= 25 && !notifiedRef.current) {
      toast.error(`Node ${nodeId} went offline!`);
      notifiedRef.current = true;
    } else if (lastSeen < 25 && notifiedRef.current) {
      // reset notification when node comes back online
      toast.success(`Node ${nodeId} is back at it!`);
      notifiedRef.current = false;
    }
  }, [lastSeen, nodeId]);

  return (
    <div className={`${bgColor} text-white p-4 rounded shadow mb-4`}>
      <h2 className="text-xl font-bold mb-2">{nodeId}</h2>
      <div className="mb-2">
        <span className="mr-4">FW: v{node.firmware ?? "?"}</span>
        <span className="mr-4">Uptime: {node.uptime ?? "?"}s</span>
        <span>Last seen: {lastSeen}s</span>
      </div>
      <div>
        {Object.entries(node.sensors).map(([name, value]) => (
          <SensorItem key={name} name={name} value={value} />
        ))}
      </div>
    </div>
  );
}