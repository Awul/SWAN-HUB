import { useEffect, useState } from "react";
import axios from "axios";
import NodeCard from "./components/NodeCard";
import { Toaster } from "react-hot-toast";

export default function App() {

  // Create a state to hold the nodes data and provide the update function
  const [nodes, setNodes] = useState({});
  // Does the same for the last updated time, so we can show it in the UI
  const [lastUpdated, setLastUpdated] = useState(null);

  // Polling function
  const fetchNodes = async () => {
    try {
      // Fetch the nodes data from the node_manager API and update the state with the response
      const res = await axios.get("http://swan-hub:8000/nodes");
      setNodes(res.data);
      setLastUpdated(new Date());
    } catch (err) {
      console.error("Failed to fetch nodes from node_manager container:", err);
    }
  };



  // Initial fetch + polling interval
  // Runs once when the component launches
  useEffect(() => {
    fetchNodes(); // initial load
    const interval = setInterval(fetchNodes, 1000); // poll every 1s
    return () => clearInterval(interval); // cleanup on unmount
  }, []);

  // Render function for displaying the node_gui, which includes a header, a list of NodeCards for each node, and the last updated time
  return (
    <div className="p-4 min-h-screen">
      <Toaster position="top-right" />
      <h1 className="text-2xl font-bold text-white mb-4">SWAN Node Dashboard</h1>

      {Object.entries(nodes).length === 0 ? (
        <p className="text-gray-500">No nodes available</p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
          {Object.entries(nodes).map(([id, node]) => (
            <NodeCard key={id} nodeId={id} node={node} />
          ))}
        </div>
      )}

      {lastUpdated && (
        <p className="text-sm text-gray-500 mt-4">
          Last updated: {lastUpdated.toLocaleTimeString()}
        </p>
      )}
    </div>
  );
}