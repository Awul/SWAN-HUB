import {
  WiThermometer,
  WiHumidity,
  WiDaySunny,
  WiStrongWind,
} from "react-icons/wi";
import {
  FaQuestion,
} from "react-icons/fa";
import { MdAudiotrack, MdAir, MdCloud } from "react-icons/md";

export default function SensorItem({ name, value }) {
  let Icon = null;
  let colorClass = "text-gray-300";

  switch (name.toLowerCase()) {
    case "temperature":
    case "temp":
      Icon = WiThermometer;
      colorClass = "text-red-400";
      break;
    case "humidity":
    case "hum":
      Icon = WiHumidity;
      colorClass = "text-blue-400";
      break;
    case "light":
      Icon = WiDaySunny;
      colorClass = "text-yellow-400";
      break;
    case "audio":
      Icon = MdAudiotrack;
      colorClass = "text-purple-400";
      break;
    case "airquality":
    case "air":
      Icon = MdAir;
      colorClass = "text-gray-200";
      break;
    case "co2":
      Icon = MdCloud;
      colorClass = "text-gray-400";
      break;
    case "pressure":
      Icon = WiStrongWind;
      colorClass = "text-indigo-400";
      break;
    default:
      Icon = FaQuestion;
      colorClass = "text-gray-300";
  }

  return (
    <div className="flex items-center justify-between border-t border-gray-700 py-1 text-sm">
      <div className="flex items-center gap-1">
        {Icon && <Icon className={`w-4 h-4 ${colorClass}`} />}
        <span>{name}</span>
      </div>
      <span>{value}</span>
    </div>
  );
}