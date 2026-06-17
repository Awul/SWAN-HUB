import { useEffect, useState } from "react";
import axios from "axios";
import NodeCard from "./components/NodeCard";
import { Toaster } from "react-hot-toast";

export default function App() {

  // Create a state to hold the nodes data and provide the update function
  const [nodes, setNodes] = useState({});
  const [lastUpdated, setLastUpdated] = useState(null);
  const [syncingNode, setSyncingNode] = useState(null);
  const [syncResult, setSyncResult] = useState(null);
  const [syncError, setSyncError] = useState(null);

  const handleSyncRead = async (nodeId) => {
    setSyncingNode(nodeId);
    setSyncResult(null);
    setSyncError(null);

    try {
      const res = await axios.get(
        `http://swan-hub:8000/nodes/${nodeId}/sync-read?timeout=5`
      );
      setSyncResult({ nodeId, data: res.data });
    } catch (err) {
      const message = err.response?.data?.detail || err.message || "Read now failed";
      setSyncError({ nodeId, message });
    } finally {
      setSyncingNode(null);
    }
  };

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
            <NodeCard
              key={id}
              nodeId={id}
              node={node}
              onSyncRead={handleSyncRead}
              isSyncing={syncingNode === id}
            />
          ))}
        </div>
      )}

      {lastUpdated && (
        <p className="text-sm text-gray-500 mt-4">
          Last updated: {lastUpdated.toLocaleTimeString()}
        </p>
      )}

      {(syncResult || syncError) && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-xl rounded bg-slate-900 p-6 text-white shadow-lg">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-xl font-semibold">Read Now Result</h2>
                <p className="text-sm text-gray-400">
                  {syncResult ? `Node ${syncResult.nodeId}` : `Node ${syncError?.nodeId}`}
                </p>
              </div>
              <button
                className="rounded bg-slate-700 px-3 py-1 text-sm hover:bg-slate-600"
                onClick={() => {
                  setSyncResult(null);
                  setSyncError(null);
                }}
              >
                Close
              </button>
            </div>

            {syncResult && (
              <pre className="max-h-96 overflow-auto rounded bg-slate-950 p-4 text-xs text-green-200">
                {JSON.stringify(syncResult.data, null, 2)}
              </pre>
            )}

            {syncError && (
              <div className="rounded bg-rose-950 p-4 text-sm text-rose-200">
                <p className="font-semibold">Error</p>
                <p>{syncError.message}</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}