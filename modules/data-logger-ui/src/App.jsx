import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Line } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  Legend,
} from 'chart.js';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend);

const API_BASE = import.meta.env.VITE_API_BASE || `http://${window.location.hostname}:8001`;
const NODE_MANAGER_API = import.meta.env.VITE_NODE_MANAGER_API || `http://${window.location.hostname}:8000`;

const COLORS = ['#2563eb', '#16a34a', '#dc2626', '#9333ea', '#ea580c', '#0891b2', '#be123c', '#4f46e5'];

function formatDate(value) {
  if (!value) return '-';
  return new Date(value).toLocaleString();
}

function comboKey(nodeId, sensorName) {
  return `${nodeId}::${sensorName}`;
}

function parseComboKey(key) {
  const [nodeId, sensorName] = key.split('::');
  return { nodeId, sensorName };
}

function normalizeNodesResponse(nodesResponse) {
  return Object.entries(nodesResponse || {}).flatMap(([nodeId, node]) => {
    const sensors = node?.sensors || {};
    return Object.entries(sensors).map(([sensorName, latestValue]) => ({
      key: comboKey(nodeId, sensorName),
      nodeId,
      sensorName,
      latestValue,
      source: 'live',
    }));
  });
}

function parseRunConfig(config) {
  if (!config) return {};
  if (typeof config === 'string') {
    try {
      return JSON.parse(config);
    } catch {
      return {};
    }
  }
  return config;
}

function runCombos(run) {
  const config = parseRunConfig(run?.config);
  return (config.nodes || []).flatMap((node) => {
    const nodeId = node.node_id || node.nodeId || node.id;
    const sensors = Array.isArray(node.sensors) ? node.sensors : [];
    if (!nodeId) return [];
    return sensors.map((sensor) => {
      const sensorName = sensor.name || sensor.sensor || sensor.sensorName;
      if (!sensorName) return null;
      return {
        key: comboKey(nodeId, sensorName),
        nodeId,
        sensorName,
        intervalSeconds: sensor.interval_seconds || sensor.intervalSeconds,
        latestValue: null,
        source: 'run',
      };
    }).filter(Boolean);
  });
}

function selectedKeys(selectionMap) {
  return Object.entries(selectionMap)
    .filter(([, config]) => config.selected)
    .map(([key]) => key);
}

function rowsToCsv(rows) {
  const columns = ['id', 'run_id', 'node_id', 'sensor', 'value', 'timestamp_utc'];
  const escape = (value) => {
    if (value === null || value === undefined) return '';
    const text = String(value);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [columns.join(','), ...rows.map((row) => columns.map((column) => escape(row[column])).join(','))].join('\n');
}

function downloadText(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function apiError(err) {
  const detail = err.response?.data?.detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg || JSON.stringify(item)).join(', ');
  return detail || err.message;
}

export default function App() {
  const [activeTab, setActiveTab] = useState('logging');
  const [runName, setRunName] = useState('swan_log');
  const [defaultInterval, setDefaultInterval] = useState(5);
  const [nodeCombos, setNodeCombos] = useState([]);
  const [loggingSelection, setLoggingSelection] = useState({});
  const [status, setStatus] = useState(null);
  const [runs, setRuns] = useState([]);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [showStopWarning, setShowStopWarning] = useState(false);

  const [selectedRunId, setSelectedRunId] = useState('');
  const [plotSelection, setPlotSelection] = useState({});
  const [fromTs, setFromTs] = useState('');
  const [toTs, setToTs] = useState('');
  const [plotLimit, setPlotLimit] = useState(500);
  const [plotRowsByKey, setPlotRowsByKey] = useState({});
  const [dataCountsByKey, setDataCountsByKey] = useState({});
  const [exportFormat, setExportFormat] = useState('csv');

  const selectedRun = runs.find((run) => run.run_id === selectedRunId);
  const plotCombos = selectedRun ? runCombos(selectedRun) : nodeCombos;
  const selectedPlotKeys = selectedKeys(plotSelection);
  const selectedLoggingKeys = selectedKeys(loggingSelection);

  useEffect(() => {
    refreshAll();
    const id = window.setInterval(fetchStatus, 5000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (!selectedRunId && runs.length) setSelectedRunId(runs[0].run_id);
  }, [runs, selectedRunId]);

  useEffect(() => {
    setPlotSelection({});
    setPlotRowsByKey({});
    setDataCountsByKey({});
  }, [selectedRunId]);

  useEffect(() => {
    if (selectedRunId) fetchDataCounts();
  }, [selectedRunId, fromTs, toTs]);

  async function refreshAll() {
    await Promise.all([fetchNodes(), fetchStatus(), fetchRuns()]);
  }

  async function fetchNodes() {
    try {
      const res = await axios.get(`${NODE_MANAGER_API}/nodes`);
      setNodeCombos(normalizeNodesResponse(res.data));
    } catch (err) {
      setError(`Could not load node list: ${apiError(err)}`);
    }
  }

  async function fetchStatus() {
    try {
      const res = await axios.get(`${API_BASE}/logging/status`);
      setStatus(res.data);
    } catch (err) {
      setError(`Could not load logging status: ${apiError(err)}`);
    }
  }

  async function fetchRuns() {
    try {
      const res = await axios.get(`${API_BASE}/logging/runs?limit=100`);
      setRuns(res.data);
    } catch (err) {
      setError(`Could not load runs: ${apiError(err)}`);
    }
  }

  async function fetchDataCounts() {
    if (!selectedRunId) return;
    try {
      const params = new URLSearchParams({ run_id: selectedRunId });
      if (fromTs) params.append('from_ts', fromTs);
      if (toTs) params.append('to_ts', toTs);
      const res = await axios.get(`${API_BASE}/data/counts?${params.toString()}`);
      setDataCountsByKey(
        Object.fromEntries(
          res.data.map((row) => [comboKey(row.node_id, row.sensor), Number(row.datapoints) || 0]),
        ),
      );
    } catch (err) {
      setError(`Could not load datapoint counts: ${apiError(err)}`);
    }
  }

  function toggleLoggingCombo(combo, selected) {
    setLoggingSelection((prev) => ({
      ...prev,
      [combo.key]: {
        selected,
        intervalSeconds: prev[combo.key]?.intervalSeconds || defaultInterval,
      },
    }));
  }

  function updateLoggingInterval(key, intervalSeconds) {
    setLoggingSelection((prev) => ({
      ...prev,
      [key]: {
        selected: prev[key]?.selected || false,
        intervalSeconds: Math.max(1, Number(intervalSeconds) || 1),
      },
    }));
  }

  function togglePlotCombo(combo, selected) {
    setPlotSelection((prev) => ({
      ...prev,
      [combo.key]: { selected },
    }));
  }

  function startPayload() {
    const grouped = new Map();
    selectedLoggingKeys.forEach((key) => {
      const { nodeId, sensorName } = parseComboKey(key);
      const intervalSeconds = Math.max(1, Number(loggingSelection[key]?.intervalSeconds) || defaultInterval);
      if (!grouped.has(nodeId)) grouped.set(nodeId, []);
      grouped.get(nodeId).push({ name: sensorName, interval_seconds: intervalSeconds });
    });

    return {
      db_name_base: runName,
      interval_seconds: Math.max(1, Number(defaultInterval) || 1),
      nodes: Array.from(grouped.entries()).map(([nodeId, sensors]) => ({
        node_id: nodeId,
        interval_seconds: Math.min(...sensors.map((sensor) => sensor.interval_seconds)),
        sensors,
      })),
    };
  }

  async function startLogging() {
    setError('');
    setMessage('');
    if (!selectedLoggingKeys.length) {
      setError('Select at least one node/sensor combination to start logging.');
      return;
    }

    try {
      const res = await axios.post(`${API_BASE}/logging/start`, startPayload());
      setMessage(`Started ${res.data.run_id}`);
      await Promise.all([fetchStatus(), fetchRuns()]);
    } catch (err) {
      setError(apiError(err));
    }
  }

  async function stopLogging() {
    setError('');
    setMessage('');
    try {
      await axios.post(`${API_BASE}/logging/stop`);
      setShowStopWarning(false);
      setMessage('Logging stopped.');
      await Promise.all([fetchStatus(), fetchRuns()]);
    } catch (err) {
      setError(apiError(err));
    }
  }

  async function loadPlotData() {
    setError('');
    setMessage('');
    if (!selectedRunId) {
      setError('Select a run session first.');
      return;
    }
    if (!selectedPlotKeys.length) {
      setError('Select at least one sensor to plot.');
      return;
    }

    try {
      const nextRows = {};
      await Promise.all(
        selectedPlotKeys.map(async (key) => {
          const { nodeId, sensorName } = parseComboKey(key);
          const params = new URLSearchParams({
            run_id: selectedRunId,
            node_id: nodeId,
            sensor: sensorName,
            limit: String(Math.min(10000, Math.max(1, Number(plotLimit) || 500))),
          });
          if (fromTs) params.append('from_ts', fromTs);
          if (toTs) params.append('to_ts', toTs);
          const res = await axios.get(`${API_BASE}/data?${params.toString()}`);
          nextRows[key] = res.data;
        }),
      );
      setPlotRowsByKey(nextRows);
    } catch (err) {
      setError(apiError(err));
    }
  }

  function exportPlottedData() {
    const rows = Object.entries(plotRowsByKey).flatMap(([key, rowsForKey]) =>
      rowsForKey.map((row) => ({ ...row, series: key })),
    );
    if (!rows.length) {
      setError('Load plot data before exporting plotted rows.');
      return;
    }
    if (exportFormat === 'json') {
      downloadText(`${selectedRunId || 'plot'}_plotted.json`, JSON.stringify(rows, null, 2), 'application/json');
      return;
    }
    downloadText(`${selectedRunId || 'plot'}_plotted.csv`, rowsToCsv(rows), 'text/csv');
  }

  async function backendExport(params, filenameSuffix = 'export') {
    const query = new URLSearchParams({ run_id: selectedRunId, format: exportFormat, ...params });
    if (fromTs) query.append('from_ts', fromTs);
    if (toTs) query.append('to_ts', toTs);
    const res = await axios.get(`${API_BASE}/export?${query.toString()}`, { responseType: 'blob' });
    const extension = exportFormat === 'json' ? 'json' : 'csv';
    const filename = `${selectedRunId}_${filenameSuffix}.${extension}`;
    const url = URL.createObjectURL(res.data);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  async function exportFullRun() {
    setError('');
    if (!selectedRunId) {
      setError('Select a run session first.');
      return;
    }
    try {
      await backendExport({}, 'full_run');
    } catch (err) {
      setError(apiError(err));
    }
  }

  async function exportSelectedSensors() {
    setError('');
    if (!selectedRunId) {
      setError('Select a run session first.');
      return;
    }
    if (!selectedPlotKeys.length) {
      setError('Select at least one sensor to export.');
      return;
    }
    try {
      for (const key of selectedPlotKeys) {
        const { nodeId, sensorName } = parseComboKey(key);
        await backendExport({ node_id: nodeId, sensor: sensorName }, `${nodeId}_${sensorName}`);
      }
    } catch (err) {
      setError(apiError(err));
    }
  }

  async function deleteSelectedRun() {
    setError('');
    setMessage('');
    if (!selectedRunId) {
      setError('Select a run session first.');
      return;
    }
    const confirmed = window.confirm(
      `Delete run session "${selectedRunId}"?\n\nThis removes its run metadata and stored readings. Active runs cannot be deleted.`,
    );
    if (!confirmed) return;

    try {
      await axios.delete(`${API_BASE}/logging/runs/${encodeURIComponent(selectedRunId)}`);
      setMessage(`Deleted ${selectedRunId}.`);
      setSelectedRunId('');
      setPlotSelection({});
      setPlotRowsByKey({});
      setDataCountsByKey({});
      await fetchRuns();
    } catch (err) {
      setError(apiError(err));
    }
  }

  const chartData = useMemo(() => {
    const labels = Array.from(
      new Set(Object.values(plotRowsByKey).flatMap((rows) => rows.map((row) => row.timestamp_utc))),
    ).sort((a, b) => new Date(a) - new Date(b));

    if (!labels.length) return null;

    return {
      labels,
      datasets: Object.entries(plotRowsByKey).map(([key, rows], index) => {
        const valueByLabel = new Map(rows.map((row) => [row.timestamp_utc, row.value]));
        return {
          label: key.replace('::', '.'),
          data: labels.map((label) => valueByLabel.get(label) ?? null),
          borderColor: COLORS[index % COLORS.length],
          backgroundColor: `${COLORS[index % COLORS.length]}33`,
          tension: 0.2,
          spanGaps: true,
        };
      }),
    };
  }, [plotRowsByKey]);

  return (
    <div style={styles.page}>
      <header style={styles.header}>
        <div>
          <h1 style={styles.title}>SWAN Data Logger</h1>
          <p style={styles.subtitle}>{API_BASE}</p>
        </div>
        <nav style={styles.tabs}>
          <button style={tabStyle(activeTab === 'logging')} onClick={() => setActiveTab('logging')}>Logging</button>
          <button style={tabStyle(activeTab === 'plot')} onClick={() => setActiveTab('plot')}>Plot & Export</button>
        </nav>
      </header>

      {error && <div style={styles.error}>{error}</div>}
      {message && <div style={styles.message}>{message}</div>}

      {activeTab === 'logging' ? (
        <LoggingTab
          runName={runName}
          setRunName={setRunName}
          defaultInterval={defaultInterval}
          setDefaultInterval={setDefaultInterval}
          nodeCombos={nodeCombos}
          loggingSelection={loggingSelection}
          status={status}
          onRefresh={refreshAll}
          onStart={startLogging}
          onStop={() => setShowStopWarning(true)}
          onToggleCombo={toggleLoggingCombo}
          onUpdateInterval={updateLoggingInterval}
        />
      ) : (
        <PlotExportTab
          runs={runs}
          selectedRunId={selectedRunId}
          setSelectedRunId={setSelectedRunId}
          plotCombos={plotCombos}
          plotSelection={plotSelection}
          dataCountsByKey={dataCountsByKey}
          fromTs={fromTs}
          setFromTs={setFromTs}
          toTs={toTs}
          setToTs={setToTs}
          plotLimit={plotLimit}
          setPlotLimit={setPlotLimit}
          exportFormat={exportFormat}
          setExportFormat={setExportFormat}
          chartData={chartData}
          onRefreshRuns={fetchRuns}
          onRefreshCounts={fetchDataCounts}
          onToggleCombo={togglePlotCombo}
          onLoadPlotData={loadPlotData}
          onExportPlotted={exportPlottedData}
          onExportSelected={exportSelectedSensors}
          onExportFullRun={exportFullRun}
          onDeleteRun={deleteSelectedRun}
        />
      )}

      {showStopWarning && (
        <div style={styles.modalBackdrop}>
          <div style={styles.modal}>
            <h2 style={styles.modalTitle}>Stop active logging?</h2>
            <p style={styles.muted}>The current run will stop and its partition will remain available for plotting and export.</p>
            <div style={styles.actions}>
              <button style={styles.secondaryButton} onClick={() => setShowStopWarning(false)}>Cancel</button>
              <button style={styles.dangerButton} onClick={stopLogging}>Stop logging</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function LoggingTab({
  runName,
  setRunName,
  defaultInterval,
  setDefaultInterval,
  nodeCombos,
  loggingSelection,
  status,
  onRefresh,
  onStart,
  onStop,
  onToggleCombo,
  onUpdateInterval,
}) {
  return (
    <main style={styles.grid}>
      <section style={styles.panel}>
        <div style={styles.panelHeader}>
          <h2 style={styles.sectionTitle}>Run setup</h2>
          <button style={styles.secondaryButton} onClick={onRefresh}>Refresh</button>
        </div>

        <div style={styles.formGrid}>
          <label style={styles.label}>
            Run name
            <input style={styles.input} value={runName} onChange={(event) => setRunName(event.target.value)} />
          </label>
          <label style={styles.label}>
            Default interval
            <input
              style={styles.input}
              type="number"
              min="1"
              value={defaultInterval}
              onChange={(event) => setDefaultInterval(Math.max(1, Number(event.target.value) || 1))}
            />
          </label>
        </div>

        <SensorSelectionTable
          combos={nodeCombos}
          selection={loggingSelection}
          defaultInterval={defaultInterval}
          intervalEditable
          onToggle={onToggleCombo}
          onUpdateInterval={onUpdateInterval}
        />

        <div style={styles.actions}>
          <button style={styles.primaryButton} onClick={onStart} disabled={status?.running}>Start logging</button>
          <button style={styles.dangerButton} onClick={onStop} disabled={!status?.running}>Stop logging</button>
        </div>
      </section>

      <StatusPanel status={status} />
    </main>
  );
}

function PlotExportTab({
  runs,
  selectedRunId,
  setSelectedRunId,
  plotCombos,
  plotSelection,
  dataCountsByKey,
  fromTs,
  setFromTs,
  toTs,
  setToTs,
  plotLimit,
  setPlotLimit,
  exportFormat,
  setExportFormat,
  chartData,
  onRefreshRuns,
  onRefreshCounts,
  onToggleCombo,
  onLoadPlotData,
  onExportPlotted,
  onExportSelected,
  onExportFullRun,
  onDeleteRun,
}) {
  return (
    <main style={styles.stack}>
      <section style={styles.panel}>
        <div style={styles.panelHeader}>
          <h2 style={styles.sectionTitle}>Run data</h2>
          <div style={styles.inlineActions}>
            <button style={styles.secondaryButton} onClick={onRefreshRuns}>Refresh runs</button>
            <button style={styles.secondaryButton} onClick={onRefreshCounts} disabled={!selectedRunId}>Refresh counts</button>
            <button style={styles.dangerButton} onClick={onDeleteRun} disabled={!selectedRunId}>Delete run</button>
          </div>
        </div>

        <div style={styles.formGrid}>
          <label style={styles.label}>
            Run session
            <select style={styles.input} value={selectedRunId} onChange={(event) => setSelectedRunId(event.target.value)}>
              <option value="">Select run</option>
              {runs.map((run) => (
                <option key={run.run_id} value={run.run_id}>{run.run_id}</option>
              ))}
            </select>
          </label>
          <label style={styles.label}>
            Plot limit
            <input
              style={styles.input}
              type="number"
              min="1"
              max="10000"
              value={plotLimit}
              onChange={(event) => setPlotLimit(Math.min(10000, Math.max(1, Number(event.target.value) || 500)))}
            />
          </label>
          <label style={styles.label}>
            From
            <input style={styles.input} value={fromTs} onChange={(event) => setFromTs(event.target.value)} placeholder="2026-06-17T08:00:00Z" />
          </label>
          <label style={styles.label}>
            To
            <input style={styles.input} value={toTs} onChange={(event) => setToTs(event.target.value)} placeholder="2026-06-17T09:00:00Z" />
          </label>
        </div>

        <SensorSelectionTable
          combos={plotCombos}
          selection={plotSelection}
          countsByKey={dataCountsByKey}
          onToggle={onToggleCombo}
        />

        <div style={styles.actions}>
          <button style={styles.primaryButton} onClick={onLoadPlotData}>Load plot</button>
          <select style={styles.selectCompact} value={exportFormat} onChange={(event) => setExportFormat(event.target.value)}>
            <option value="csv">CSV</option>
            <option value="json">JSON</option>
          </select>
          <button style={styles.secondaryButton} onClick={onExportPlotted}>Export plotted</button>
          <button style={styles.secondaryButton} onClick={onExportSelected}>Export selected sensors</button>
          <button style={styles.secondaryButton} onClick={onExportFullRun}>Export full run</button>
        </div>
      </section>

      <section style={styles.panel}>
        <h2 style={styles.sectionTitle}>Time series</h2>
        <div style={styles.chartBox}>
          {chartData ? <Line data={chartData} options={chartOptions} /> : <div style={styles.empty}>No plot data loaded.</div>}
        </div>
      </section>
    </main>
  );
}

function SensorSelectionTable({
  combos,
  selection,
  countsByKey = null,
  defaultInterval = 5,
  intervalEditable = false,
  onToggle,
  onUpdateInterval,
}) {
  if (!combos.length) {
    return <div style={styles.empty}>No node sensors available.</div>;
  }

  const showCounts = !!countsByKey;
  const grouped = combos.reduce((acc, combo) => {
    if (!acc[combo.nodeId]) acc[combo.nodeId] = [];
    acc[combo.nodeId].push(combo);
    return acc;
  }, {});
  const allSelected = combos.every((combo) => selection[combo.key]?.selected);

  const toggleMany = (items, selected) => {
    items.forEach((combo) => onToggle(combo, selected));
  };

  return (
    <div style={styles.tableWrap}>
      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>
              <label style={styles.checkLabel}>
                <input type="checkbox" checked={allSelected} onChange={(event) => toggleMany(combos, event.target.checked)} />
                All
              </label>
            </th>
            <th style={styles.th}>Node</th>
            <th style={styles.th}>Sensor</th>
            <th style={styles.th}>Latest</th>
            {showCounts && <th style={styles.th}>Points</th>}
            {intervalEditable && <th style={styles.th}>Interval</th>}
          </tr>
        </thead>
        <tbody>
          {Object.entries(grouped).map(([nodeId, nodeCombos]) => {
            const selectedCount = nodeCombos.filter((combo) => selection[combo.key]?.selected).length;
            const nodeSelected = selectedCount === nodeCombos.length;
            return (
              <FragmentGroup
                key={nodeId}
                nodeId={nodeId}
                nodeCombos={nodeCombos}
                selectedCount={selectedCount}
                nodeSelected={nodeSelected}
                selection={selection}
                countsByKey={countsByKey}
                showCounts={showCounts}
                intervalEditable={intervalEditable}
                defaultInterval={defaultInterval}
                onToggle={onToggle}
                onToggleNode={(selected) => toggleMany(nodeCombos, selected)}
                onUpdateInterval={onUpdateInterval}
              />
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function FragmentGroup({
  nodeId,
  nodeCombos,
  selectedCount,
  nodeSelected,
  selection,
  countsByKey,
  showCounts,
  intervalEditable,
  defaultInterval,
  onToggle,
  onToggleNode,
  onUpdateInterval,
}) {
  return (
    <>
      <tr>
        <td style={styles.nodeHeaderCell}>
          <input type="checkbox" checked={nodeSelected} onChange={(event) => onToggleNode(event.target.checked)} />
        </td>
        <td style={styles.nodeHeaderCell} colSpan={(intervalEditable ? 4 : 3) + (showCounts ? 1 : 0)}>
          <span style={styles.nodeHeaderText}>{nodeId}</span>
          <span style={styles.nodeHeaderCount}>{selectedCount}/{nodeCombos.length} selected</span>
        </td>
      </tr>
      {nodeCombos.map((combo) => {
        const row = selection[combo.key] || {};
        return (
          <tr key={combo.key}>
            <td style={styles.td}>
              <input type="checkbox" checked={!!row.selected} onChange={(event) => onToggle(combo, event.target.checked)} />
            </td>
            <td style={styles.td}>{combo.nodeId}</td>
            <td style={styles.td}>{combo.sensorName}</td>
            <td style={styles.td}>{combo.latestValue ?? '-'}</td>
            {showCounts && <td style={styles.td}>{countsByKey?.[combo.key] ?? 0}</td>}
            {intervalEditable && (
              <td style={styles.td}>
                <input
                  style={styles.intervalInput}
                  type="number"
                  min="1"
                  value={row.intervalSeconds || combo.intervalSeconds || defaultInterval}
                  onChange={(event) => onUpdateInterval(combo.key, event.target.value)}
                />
              </td>
            )}
          </tr>
        );
      })}
    </>
  );
}

function StatusPanel({ status }) {
  const running = !!status?.running;
  return (
    <section style={styles.panel}>
      <div style={styles.panelHeader}>
        <h2 style={styles.sectionTitle}>Status</h2>
        <span style={running ? styles.runningBadge : styles.stoppedBadge}>{running ? 'Running' : 'Stopped'}</span>
      </div>

      <div style={styles.statusGrid}>
        <Metric label="Run" value={status?.run_id || '-'} />
        <Metric label="Database" value={status?.db_name || '-'} />
        <Metric label="Table" value={status?.table_name || '-'} />
        {status?.partition_name && <Metric label="Partition" value={status.partition_name} />}
        <Metric label="Started" value={formatDate(status?.started_at)} />
        <Metric label="Grid" value={status?.grid_interval_seconds ? `${status.grid_interval_seconds}s` : '-'} />
      </div>

      <div style={styles.statusSensors}>
        {(status?.nodes || []).map((node) => (
          <div key={node.node_id} style={styles.nodeBlock}>
            <strong>{node.node_id}</strong>
            <div style={styles.sensorPills}>
              {(node.sensors || []).map((sensor) => (
                <span key={`${node.node_id}-${sensor.name}`} style={styles.sensorPill}>
                  {sensor.name} · {sensor.interval_seconds}s
                </span>
              ))}
            </div>
          </div>
        ))}
        {!running && <div style={styles.empty}>No active run.</div>}
      </div>
    </section>
  );
}

function Metric({ label, value }) {
  return (
    <div style={styles.metric}>
      <div style={styles.metricLabel}>{label}</div>
      <div style={styles.metricValue}>{value}</div>
    </div>
  );
}

const chartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  interaction: { mode: 'index', intersect: false },
  plugins: {
    legend: { labels: { color: '#dbeafe' } },
  },
  scales: {
    x: { ticks: { color: '#94a3b8', maxRotation: 45, minRotation: 0 }, grid: { color: '#1f2937' } },
    y: { ticks: { color: '#94a3b8' }, grid: { color: '#1f2937' } },
  },
};

function tabStyle(active) {
  return {
    ...styles.tab,
    background: active ? '#2563eb' : '#111827',
    borderColor: active ? '#60a5fa' : '#334155',
  };
}

const styles = {
  page: {
    minHeight: '100vh',
    background: '#0b1120',
    color: '#e5e7eb',
    fontFamily: 'Inter, system-ui, sans-serif',
    padding: 24,
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    gap: 16,
    alignItems: 'center',
    marginBottom: 18,
  },
  title: { margin: 0, fontSize: 28, fontWeight: 700 },
  subtitle: { margin: '4px 0 0', color: '#94a3b8', fontSize: 13 },
  tabs: { display: 'flex', gap: 8 },
  tab: {
    color: '#fff',
    border: '1px solid',
    borderRadius: 8,
    padding: '10px 14px',
    cursor: 'pointer',
  },
  grid: { display: 'grid', gridTemplateColumns: 'minmax(0, 1.35fr) minmax(340px, 0.65fr)', gap: 18 },
  stack: { display: 'grid', gap: 18 },
  panel: { background: '#111827', border: '1px solid #243044', borderRadius: 8, padding: 18 },
  panelHeader: { display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 14 },
  inlineActions: { display: 'flex', flexWrap: 'wrap', justifyContent: 'flex-end', gap: 8 },
  sectionTitle: { margin: 0, fontSize: 18 },
  formGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12, marginBottom: 16 },
  label: { display: 'grid', gap: 6, color: '#cbd5e1', fontSize: 13 },
  input: { background: '#0f172a', color: '#fff', border: '1px solid #334155', borderRadius: 6, padding: '9px 10px' },
  selectCompact: { background: '#0f172a', color: '#fff', border: '1px solid #334155', borderRadius: 6, padding: '9px 10px' },
  tableWrap: { overflowX: 'auto', border: '1px solid #243044', borderRadius: 8 },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 14 },
  th: { textAlign: 'left', color: '#93c5fd', background: '#0f172a', padding: '10px 12px', borderBottom: '1px solid #243044' },
  td: { padding: '10px 12px', borderBottom: '1px solid #1f2937', color: '#e5e7eb' },
  checkLabel: { display: 'inline-flex', alignItems: 'center', gap: 8 },
  nodeHeaderCell: { padding: '9px 12px', background: '#172033', borderBottom: '1px solid #243044', color: '#dbeafe' },
  nodeHeaderText: { fontWeight: 700, marginRight: 10 },
  nodeHeaderCount: { color: '#94a3b8', fontSize: 12 },
  intervalInput: { width: 90, background: '#0f172a', color: '#fff', border: '1px solid #334155', borderRadius: 6, padding: '7px 8px' },
  actions: { display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 16 },
  primaryButton: { background: '#2563eb', color: '#fff', border: 0, borderRadius: 8, padding: '10px 14px', cursor: 'pointer' },
  secondaryButton: { background: '#334155', color: '#fff', border: 0, borderRadius: 8, padding: '10px 14px', cursor: 'pointer' },
  dangerButton: { background: '#dc2626', color: '#fff', border: 0, borderRadius: 8, padding: '10px 14px', cursor: 'pointer' },
  runningBadge: { color: '#052e16', background: '#86efac', borderRadius: 999, padding: '6px 10px', fontWeight: 700 },
  stoppedBadge: { color: '#450a0a', background: '#fca5a5', borderRadius: 999, padding: '6px 10px', fontWeight: 700 },
  statusGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 },
  metric: { background: '#0f172a', border: '1px solid #243044', borderRadius: 8, padding: 10, minWidth: 0 },
  metricLabel: { color: '#94a3b8', fontSize: 12, marginBottom: 4 },
  metricValue: { color: '#fff', fontSize: 13, wordBreak: 'break-word' },
  statusSensors: { display: 'grid', gap: 10, marginTop: 14 },
  nodeBlock: { background: '#0f172a', border: '1px solid #243044', borderRadius: 8, padding: 10 },
  sensorPills: { display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 },
  sensorPill: { background: '#1d4ed8', color: '#dbeafe', borderRadius: 999, padding: '5px 9px', fontSize: 12 },
  chartBox: { height: 420, background: '#0f172a', border: '1px solid #243044', borderRadius: 8, padding: 12 },
  empty: { color: '#94a3b8', padding: 14 },
  error: { background: '#7f1d1d', color: '#fee2e2', borderRadius: 8, padding: 12, marginBottom: 12 },
  message: { background: '#14532d', color: '#dcfce7', borderRadius: 8, padding: 12, marginBottom: 12 },
  modalBackdrop: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0,0,0,0.65)',
    display: 'grid',
    placeItems: 'center',
    padding: 20,
  },
  modal: { background: '#111827', border: '1px solid #334155', borderRadius: 8, padding: 20, width: 'min(440px, 100%)' },
  modalTitle: { margin: '0 0 10px' },
  muted: { color: '#94a3b8' },
};
