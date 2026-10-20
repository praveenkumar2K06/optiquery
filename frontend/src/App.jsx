import React, { useState, useEffect, useRef } from 'react';
import {
  Database,
  Play,
  Zap,
  Code2,
  FileCode,
  Layers,
  Activity,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Copy,
  Check,
  RotateCcw,
  BookOpen,
  ArrowRight,
  TrendingDown,
  Table,
  Settings2,
  ListTree,
  FileJson,
  Cpu,
  ChevronRight,
  ChevronDown,
  ExternalLink
} from 'lucide-react';

export default function App() {
  const [sql, setSql] = useState('SELECT name, age FROM users WHERE age >= 18 ORDER BY age DESC LIMIT 5');
  const [backend, setBackend] = useState('mongodb');
  const [enableOptimizations, setEnableOptimizations] = useState(true);
  const [selectedSchema, setSelectedSchema] = useState('ecommerce');
  const [schemas, setSchemas] = useState({});
  const [presets, setPresets] = useState([]);
  const [activeTab, setActiveTab] = useState('query');
  const [compiling, setCompiling] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [compileResult, setCompileResult] = useState(null);
  const [executeResult, setExecuteResult] = useState(null);
  const [copied, setCopied] = useState(false);
  const [customSchemaJson, setCustomSchemaJson] = useState('');
  const [useCustomSchema, setUseCustomSchema] = useState(false);
  const [schemaError, setSchemaError] = useState(null);
  const [showSchemaDrawer, setShowSchemaDrawer] = useState(false);
  const textareaRef = useRef(null);

  // Fetch initial schemas and presets
  useEffect(() => {
    async function loadMeta() {
      try {
        const [schemasRes, presetsRes] = await Promise.all([
          fetch('/api/schemas').then(r => r.json()),
          fetch('/api/presets').then(r => r.json())
        ]);
        const map = {};
        schemasRes.forEach(s => { map[s.id] = s; });
        setSchemas(map);
        setPresets(presetsRes);
        if (map['ecommerce']) {
          setCustomSchemaJson(JSON.stringify(map['ecommerce'].schema_dict, null, 2));
        }
      } catch (err) {
        console.error('Failed to load metadata:', err);
      }
    }
    loadMeta();
  }, []);

  // Run initial compile
  useEffect(() => {
    handleCompile();
  }, [backend, enableOptimizations, selectedSchema, useCustomSchema]);

  // Handle keyboard shortcut (Ctrl+Enter or Cmd+Enter to compile)
  const handleKeyDown = (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      handleCompile();
    }
  };

  const handleCompile = async () => {
    if (!sql.trim()) return;
    setCompiling(true);
    let customObj = null;
    if (useCustomSchema) {
      try {
        customObj = JSON.parse(customSchemaJson);
        setSchemaError(null);
      } catch (e) {
        setSchemaError('Invalid JSON in custom schema');
        setCompiling(false);
        return;
      }
    }

    try {
      const res = await fetch('/api/compile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sql,
          backend,
          schema_name: selectedSchema,
          custom_schema: customObj,
          enable_optimizations: enableOptimizations,
          strict_catalog: false
        })
      });
      const data = await res.json();
      setCompileResult(data);
    } catch (err) {
      console.error('Compilation failed:', err);
    } finally {
      setCompiling(false);
    }
  };

  const handleExecute = async () => {
    setExecuting(true);
    let customObj = null;
    if (useCustomSchema) {
      try {
        customObj = JSON.parse(customSchemaJson);
      } catch {
        setExecuting(false);
        return;
      }
    }

    try {
      const res = await fetch('/api/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sql,
          backend,
          schema_name: selectedSchema,
          custom_schema: customObj,
          enable_optimizations: enableOptimizations
        })
      });
      const data = await res.json();
      setExecuteResult(data);
      setActiveTab('simulation');
    } catch (err) {
      console.error('Execution failed:', err);
    } finally {
      setExecuting(false);
    }
  };

  const handlePresetSelect = (preset) => {
    setSql(preset.sql);
    setBackend(preset.backend);
    setSelectedSchema(preset.schema_name);
    setUseCustomSchema(false);
  };

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const insertSnippet = (snippet) => {
    const el = textareaRef.current;
    if (!el) return;
    const start = el.selectionStart;
    const end = el.selectionEnd;
    const nextSql = sql.substring(0, start) + ' ' + snippet + ' ' + sql.substring(end);
    setSql(nextSql);
    setTimeout(() => {
      el.focus();
      el.setSelectionRange(start + snippet.length + 2, start + snippet.length + 2);
    }, 10);
  };

  const currentSchemaObj = schemas[selectedSchema]?.schema_dict || {};
  const collections = currentSchemaObj.collections || {};

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-zinc-950 text-zinc-100 font-sans">
      {/* Top Navbar */}
      <header className="flex items-center justify-between px-5 py-3 border-b border-zinc-800 bg-zinc-900/80 backdrop-blur z-20">
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-indigo-600 shadow-md shadow-indigo-500/20 text-white font-bold">
            <Zap className="w-5 h-5 text-indigo-100" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-bold text-base tracking-tight text-white">OptiQuery</span>
              <span className="text-xs px-2 py-0.5 rounded-full bg-indigo-950/80 text-indigo-400 border border-indigo-800/60 font-medium">
                SQL-to-NoSQL Compiler
              </span>
            </div>
            <p className="text-[11px] text-zinc-400">Cost-based query optimizer & AST generator</p>
          </div>
        </div>

        {/* Center Controls: Backend & Optimizer */}
        <div className="flex items-center gap-3">
          {/* Target Backend Selector */}
          <div className="flex items-center p-1 rounded-lg bg-zinc-950 border border-zinc-800 text-xs">
            <button
              onClick={() => setBackend('mongodb')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md font-medium transition-all ${
                backend === 'mongodb'
                  ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 shadow-sm'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <Database className="w-3.5 h-3.5" />
              <span>MongoDB Pipeline</span>
            </button>
            <button
              onClick={() => setBackend('redis')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md font-medium transition-all ${
                backend === 'redis'
                  ? 'bg-rose-500/15 text-rose-400 border border-rose-500/30 shadow-sm'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <Database className="w-3.5 h-3.5" />
              <span>Redis Commands</span>
            </button>
          </div>

          {/* Optimizer Toggle */}
          <button
            onClick={() => setEnableOptimizations(!enableOptimizations)}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-medium transition-all ${
              enableOptimizations
                ? 'bg-indigo-500/10 text-indigo-300 border-indigo-500/30'
                : 'bg-zinc-900 text-zinc-500 border-zinc-800'
            }`}
            title="Toggle rule passes (projection pushdown, filter reordering, constant folding, limit pushdown, CSE)"
          >
            <Cpu className={`w-3.5 h-3.5 ${enableOptimizations ? 'text-indigo-400' : 'text-zinc-500'}`} />
            <span>Optimizer: {enableOptimizations ? 'ON' : 'OFF'}</span>
          </button>

          {/* Schema Selector */}
          <select
            value={useCustomSchema ? 'custom' : selectedSchema}
            onChange={(e) => {
              if (e.target.value === 'custom') {
                setUseCustomSchema(true);
              } else {
                setUseCustomSchema(false);
                setSelectedSchema(e.target.value);
                if (schemas[e.target.value]) {
                  setCustomSchemaJson(JSON.stringify(schemas[e.target.value].schema_dict, null, 2));
                }
              }
            }}
            className="bg-zinc-950 border border-zinc-800 text-xs text-zinc-300 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-indigo-500"
          >
            <option value="ecommerce">Schema: E-Commerce (Users & Orders)</option>
            <option value="analytics">Schema: Web Analytics (Pageviews)</option>
            <option value="custom">Schema: Custom JSON Schema...</option>
          </select>

          {/* Presets Dropdown */}
          <div className="relative group">
            <button className="flex items-center gap-1.5 px-3 py-1.5 bg-zinc-950 border border-zinc-800 hover:border-zinc-700 text-zinc-300 text-xs rounded-lg font-medium">
              <BookOpen className="w-3.5 h-3.5 text-zinc-400" />
              <span>Sample Queries</span>
              <ChevronDown className="w-3 h-3 text-zinc-500" />
            </button>
            <div className="absolute right-0 top-full mt-1.5 w-72 bg-zinc-900 border border-zinc-800 rounded-xl shadow-xl py-2 hidden group-hover:block z-50">
              <div className="px-3 py-1 text-[10px] font-semibold tracking-wider text-zinc-400 uppercase">
                Featured Queries
              </div>
              {presets.map((p) => (
                <button
                  key={p.id}
                  onClick={() => handlePresetSelect(p)}
                  className="w-full text-left px-3 py-2 text-xs hover:bg-zinc-800/70 transition-colors flex flex-col gap-0.5 border-b border-zinc-800/40 last:border-b-0"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-zinc-200">{p.title}</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 font-mono">
                      {p.backend}
                    </span>
                  </div>
                  <span className="text-[11px] text-zinc-400 line-clamp-1">{p.description}</span>
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Right Actions: Compile, Execute, Swagger Docs */}
        <div className="flex items-center gap-2">
          <button
            onClick={handleCompile}
            disabled={compiling}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 active:bg-indigo-700 text-white text-xs font-semibold shadow-sm transition-all"
            title="Compile SQL to target NoSQL backend (Ctrl+Enter)"
          >
            <Zap className={`w-3.5 h-3.5 ${compiling ? 'animate-spin' : ''}`} />
            <span>Compile</span>
            <kbd className="hidden sm:inline-block ml-1 text-[10px] px-1 rounded bg-indigo-700 text-indigo-200 font-mono">
              ⌘↵
            </kbd>
          </button>

          <button
            onClick={handleExecute}
            disabled={executing}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 active:bg-emerald-700 text-white text-xs font-semibold shadow-sm transition-all"
            title="Simulate query execution on sample dataset"
          >
            <Play className={`w-3.5 h-3.5 ${executing ? 'animate-pulse' : ''}`} />
            <span>Simulate Run</span>
          </button>

          <a
            href="/docs"
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 text-xs transition-colors"
            title="View FastAPI Swagger Interactive Documentation"
          >
            <span>API Docs</span>
            <ExternalLink className="w-3 h-3 text-zinc-500" />
          </a>
        </div>
      </header>

      {/* Main Workspace Layout */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left Half: SQL Editor & Schema Inspector */}
        <div className="w-1/2 flex flex-col border-r border-zinc-800 bg-zinc-950">
          {/* Editor Sub-header */}
          <div className="flex items-center justify-between px-4 py-2 bg-zinc-900/40 border-b border-zinc-800 text-xs">
            <div className="flex items-center gap-2 text-zinc-400 font-medium">
              <Code2 className="w-4 h-4 text-indigo-400" />
              <span>SQL Query Input</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] text-zinc-500 mr-1">Insert:</span>
              {['SELECT', 'WHERE', 'GROUP BY', 'ORDER BY', 'LIMIT'].map((kw) => (
                <button
                  key={kw}
                  onClick={() => insertSnippet(kw)}
                  className="px-2 py-0.5 rounded bg-zinc-900 hover:bg-zinc-800 text-zinc-300 border border-zinc-800 text-[11px] font-mono transition-colors"
                >
                  {kw}
                </button>
              ))}
              <button
                onClick={() => setShowSchemaDrawer(!showSchemaDrawer)}
                className={`ml-2 px-2 py-0.5 rounded border text-[11px] font-medium transition-colors ${
                  showSchemaDrawer
                    ? 'bg-indigo-500/20 text-indigo-300 border-indigo-500/40'
                    : 'bg-zinc-900 text-zinc-400 border-zinc-800 hover:text-zinc-200'
                }`}
              >
                Schema Fields
              </button>
            </div>
          </div>

          {/* Schema Drawer (collapsible) */}
          {showSchemaDrawer && (
            <div className="p-3 bg-zinc-900/60 border-b border-zinc-800 text-xs max-h-48 overflow-y-auto">
              <div className="flex items-center justify-between mb-2">
                <span className="font-semibold text-zinc-300 text-[11px] uppercase tracking-wider">
                  Active Schema: {schemas[selectedSchema]?.name || 'Custom'}
                </span>
                <span className="text-[10px] text-zinc-500">Click a column to insert</span>
              </div>
              <div className="grid grid-cols-2 gap-3">
                {Object.entries(collections).map(([collName, collSpec]) => (
                  <div key={collName} className="p-2 rounded-lg bg-zinc-950 border border-zinc-800/80">
                    <div className="font-mono text-indigo-400 font-semibold mb-1 flex items-center justify-between">
                      <span>{collName}</span>
                      <span className="text-[10px] text-zinc-500">{collSpec.cardinality || 0} rows</span>
                    </div>
                    <div className="space-y-1">
                      {Object.entries(collSpec.fields || {}).map(([fname, fspec]) => (
                        <div
                          key={fname}
                          onClick={() => insertSnippet(fname)}
                          className="flex items-center justify-between py-0.5 px-1 rounded hover:bg-zinc-800/50 cursor-pointer font-mono text-[11px]"
                        >
                          <span className="text-zinc-300">{fname}</span>
                          <div className="flex items-center gap-1 text-[10px]">
                            <span className="text-zinc-500">{fspec.type}</span>
                            {fspec.indexed && (
                              <span className="px-1 rounded bg-emerald-950/80 text-emerald-400 text-[9px] border border-emerald-800/50">
                                IDX
                              </span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* SQL Editor Area */}
          <div className="flex-1 relative flex">
            <textarea
              ref={textareaRef}
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              onKeyDown={handleKeyDown}
              spellCheck="false"
              className="w-full h-full p-4 bg-transparent font-mono text-sm leading-relaxed text-zinc-100 resize-none focus:outline-none selection:bg-indigo-600/30 code-editor"
              placeholder="Enter standard SQL query, e.g. SELECT name, age FROM users WHERE age >= 18..."
            />
          </div>

          {/* Real-time Diagnostics Banner at bottom of editor */}
          {compileResult?.diagnostics && compileResult.diagnostics.length > 0 && (
            <div className="border-t border-zinc-800 bg-zinc-950 max-h-36 overflow-y-auto">
              {compileResult.diagnostics.map((d, i) => (
                <div
                  key={i}
                  className={`flex items-start gap-2.5 px-4 py-2 border-b border-zinc-900 text-xs ${
                    d.severity === 'error'
                      ? 'bg-rose-950/20 text-rose-300'
                      : 'bg-amber-950/20 text-amber-300'
                  }`}
                >
                  {d.severity === 'error' ? (
                    <XCircle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                  ) : (
                    <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                  )}
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-semibold px-1 py-0.2 rounded bg-zinc-900 text-zinc-300 text-[10px]">
                        {d.code}
                      </span>
                      {d.line && (
                        <span className="text-[11px] text-zinc-400">
                          Line {d.line}, Column {d.column}
                        </span>
                      )}
                    </div>
                    <div className="mt-0.5 text-xs text-zinc-200">{d.message}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right Half: Compiler Stages & Results Tabbed Workspace */}
        <div className="w-1/2 flex flex-col bg-zinc-950 overflow-hidden">
          {/* Output Stage Tabs */}
          <div className="flex items-center justify-between px-3 border-b border-zinc-800 bg-zinc-900/50 text-xs">
            <div className="flex items-center space-x-1">
              <button
                onClick={() => setActiveTab('query')}
                className={`flex items-center gap-1.5 px-3 py-2.5 font-medium border-b-2 transition-colors ${
                  activeTab === 'query'
                    ? 'border-indigo-500 text-indigo-400 bg-indigo-500/5'
                    : 'border-transparent text-zinc-400 hover:text-zinc-200'
                }`}
              >
                <Database className="w-3.5 h-3.5" />
                <span>Generated Query</span>
                {compileResult?.ok && (
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 ml-1"></span>
                )}
              </button>

              <button
                onClick={() => setActiveTab('plans')}
                className={`flex items-center gap-1.5 px-3 py-2.5 font-medium border-b-2 transition-colors ${
                  activeTab === 'plans'
                    ? 'border-indigo-500 text-indigo-400 bg-indigo-500/5'
                    : 'border-transparent text-zinc-400 hover:text-zinc-200'
                }`}
              >
                <Layers className="w-3.5 h-3.5" />
                <span>Plan Comparison</span>
              </button>

              <button
                onClick={() => setActiveTab('optimizer')}
                className={`flex items-center gap-1.5 px-3 py-2.5 font-medium border-b-2 transition-colors ${
                  activeTab === 'optimizer'
                    ? 'border-indigo-500 text-indigo-400 bg-indigo-500/5'
                    : 'border-transparent text-zinc-400 hover:text-zinc-200'
                }`}
              >
                <Activity className="w-3.5 h-3.5" />
                <span>Passes & Cost</span>
                {compileResult?.cost_comparison?.savings_pct > 0 && (
                  <span className="text-[10px] px-1.5 py-0.2 rounded bg-emerald-950 text-emerald-400 border border-emerald-800">
                    -{compileResult.cost_comparison.savings_pct}%
                  </span>
                )}
              </button>

              <button
                onClick={() => setActiveTab('ast')}
                className={`flex items-center gap-1.5 px-3 py-2.5 font-medium border-b-2 transition-colors ${
                  activeTab === 'ast'
                    ? 'border-indigo-500 text-indigo-400 bg-indigo-500/5'
                    : 'border-transparent text-zinc-400 hover:text-zinc-200'
                }`}
              >
                <ListTree className="w-3.5 h-3.5" />
                <span>AST & Tokens</span>
              </button>

              <button
                onClick={() => setActiveTab('simulation')}
                className={`flex items-center gap-1.5 px-3 py-2.5 font-medium border-b-2 transition-colors ${
                  activeTab === 'simulation'
                    ? 'border-indigo-500 text-indigo-400 bg-indigo-500/5'
                    : 'border-transparent text-zinc-400 hover:text-zinc-200'
                }`}
              >
                <Table className="w-3.5 h-3.5" />
                <span>Run / Results</span>
                {executeResult && (
                  <span className="text-[10px] px-1.5 py-0.2 rounded bg-zinc-800 text-zinc-300">
                    {executeResult.row_count}
                  </span>
                )}
              </button>

              <button
                onClick={() => setActiveTab('schema')}
                className={`flex items-center gap-1.5 px-3 py-2.5 font-medium border-b-2 transition-colors ${
                  activeTab === 'schema'
                    ? 'border-indigo-500 text-indigo-400 bg-indigo-500/5'
                    : 'border-transparent text-zinc-400 hover:text-zinc-200'
                }`}
              >
                <Settings2 className="w-3.5 h-3.5" />
                <span>Schema JSON</span>
              </button>
            </div>

            {/* Quick Status / Copy Action */}
            <div className="flex items-center gap-2 py-1">
              {compileResult?.query_formatted && (
                <button
                  onClick={() => copyToClipboard(compileResult.query_formatted)}
                  className="flex items-center gap-1 px-2.5 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs transition-colors"
                  title="Copy generated query to clipboard"
                >
                  {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                  <span>{copied ? 'Copied!' : 'Copy'}</span>
                </button>
              )}
            </div>
          </div>

          {/* Tab Contents Area */}
          <div className="flex-1 overflow-y-auto p-4">
            {/* TAB 1: Generated NoSQL Query */}
            {activeTab === 'query' && (
              <div className="space-y-4">
                {/* Status Callout */}
                <div className="flex items-center justify-between p-3 rounded-lg bg-zinc-900 border border-zinc-800">
                  <div className="flex items-center gap-2">
                    {compileResult?.ok ? (
                      <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                    ) : (
                      <XCircle className="w-4 h-4 text-rose-400" />
                    )}
                    <span className="font-semibold text-xs text-zinc-200">
                      Target: {compileResult?.backend === 'mongodb' ? 'MongoDB Aggregation' : 'Redis Key-Value'}
                    </span>
                    {compileResult?.collection && (
                      <span className="text-xs px-2 py-0.5 rounded bg-zinc-800 text-zinc-400 font-mono">
                        collection: {compileResult.collection}
                      </span>
                    )}
                  </div>
                  {compileResult?.cost_comparison && (
                    <div className="text-xs text-zinc-400 flex items-center gap-1 font-mono">
                      <span>Cost: {compileResult.cost_comparison.optimized_cost}</span>
                      {compileResult.cost_comparison.savings_pct > 0 && (
                        <span className="text-emerald-400 font-semibold">
                          (-{compileResult.cost_comparison.savings_pct}%)
                        </span>
                      )}
                    </div>
                  )}
                </div>

                {/* Pipeline Stages Breakdown (if MongoDB) */}
                {compileResult?.backend === 'mongodb' && Array.isArray(compileResult?.generated_query) && (
                  <div>
                    <div className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider mb-2">
                      Pipeline Execution Steps
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5 p-2 rounded-lg bg-zinc-900/60 border border-zinc-800/80">
                      {compileResult.generated_query.map((stg, idx) => {
                        const stageName = Object.keys(stg)[0];
                        return (
                          <React.Fragment key={idx}>
                            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-zinc-800 border border-zinc-700/60 text-xs font-mono font-medium text-emerald-400">
                              <span className="text-[10px] text-zinc-500">#{idx + 1}</span>
                              <span>{stageName}</span>
                            </div>
                            {idx < compileResult.generated_query.length - 1 && (
                              <ArrowRight className="w-3 h-3 text-zinc-600" />
                            )}
                          </React.Fragment>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* Query Code Output */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider">
                      Generated Code
                    </span>
                    <span className="text-[11px] text-zinc-500 font-mono">
                      {compileResult?.backend === 'mongodb' ? 'db.collection.aggregate([...])' : 'CLI command batch'}
                    </span>
                  </div>
                  <pre className="p-4 rounded-xl bg-zinc-900/90 border border-zinc-800 text-xs font-mono text-emerald-300 leading-relaxed overflow-x-auto shadow-inner">
                    {compileResult?.query_formatted || (compileResult?.ok ? '[]' : '/* No query generated due to compiler diagnostics */')}
                  </pre>
                </div>

                {/* Backend Notes */}
                {compileResult?.notes && compileResult.notes.length > 0 && (
                  <div className="p-3 rounded-lg bg-zinc-900/40 border border-zinc-800/60 text-xs space-y-1">
                    <div className="text-[11px] font-semibold text-zinc-400">Compiler Notes:</div>
                    {compileResult.notes.map((n, i) => (
                      <div key={i} className="text-zinc-300 flex items-center gap-2">
                        <span className="w-1.5 h-1.5 rounded-full bg-indigo-400"></span>
                        <span>{n}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* TAB 2: Plan Comparison (Original vs Optimized) */}
            {activeTab === 'plans' && (
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">
                        Initial Logical Plan (Lowered)
                      </span>
                      <span className="text-[10px] px-2 py-0.5 rounded bg-zinc-800 text-zinc-400 font-mono">
                        Cost: {compileResult?.cost_comparison?.original_cost || 0}
                      </span>
                    </div>
                    <pre className="p-3.5 rounded-xl bg-zinc-900/90 border border-zinc-800 text-xs font-mono text-zinc-300 leading-relaxed overflow-x-auto min-h-[300px]">
                      {compileResult?.initial_plan_text || 'No plan available'}
                    </pre>
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-semibold text-indigo-400 uppercase tracking-wider flex items-center gap-1.5">
                        <Zap className="w-3.5 h-3.5" />
                        Optimized Logical Plan
                      </span>
                      <span className="text-[10px] px-2 py-0.5 rounded bg-indigo-950 text-indigo-300 border border-indigo-800 font-mono">
                        Cost: {compileResult?.cost_comparison?.optimized_cost || 0}
                      </span>
                    </div>
                    <pre className="p-3.5 rounded-xl bg-zinc-900/90 border border-indigo-900/40 text-xs font-mono text-indigo-200 leading-relaxed overflow-x-auto min-h-[300px]">
                      {compileResult?.optimized_plan_text || 'No optimized plan available'}
                    </pre>
                  </div>
                </div>

                {compileResult?.selection_rationale && (
                  <div className="p-3 rounded-lg bg-indigo-950/20 border border-indigo-900/40 text-xs text-indigo-300">
                    <span className="font-semibold">Optimizer Decision:</span> {compileResult.selection_rationale}
                  </div>
                )}
              </div>
            )}

            {/* TAB 3: Passes & Cost Breakdown */}
            {activeTab === 'optimizer' && (
              <div className="space-y-5">
                {/* Cost Comparison Summary Cards */}
                {compileResult?.cost_comparison && (
                  <div className="grid grid-cols-3 gap-3">
                    <div className="p-3.5 rounded-xl bg-zinc-900 border border-zinc-800">
                      <div className="text-[11px] text-zinc-400 mb-1">Original Plan Cost</div>
                      <div className="text-lg font-bold font-mono text-zinc-200">
                        {compileResult.cost_comparison.original_cost}
                      </div>
                    </div>
                    <div className="p-3.5 rounded-xl bg-zinc-900 border border-zinc-800">
                      <div className="text-[11px] text-zinc-400 mb-1">Optimized Plan Cost</div>
                      <div className="text-lg font-bold font-mono text-indigo-400">
                        {compileResult.cost_comparison.optimized_cost}
                      </div>
                    </div>
                    <div className="p-3.5 rounded-xl bg-emerald-950/30 border border-emerald-900/50">
                      <div className="text-[11px] text-emerald-400 mb-1 flex items-center gap-1">
                        <TrendingDown className="w-3.5 h-3.5" />
                        Estimated Improvement
                      </div>
                      <div className="text-lg font-bold font-mono text-emerald-300">
                        {compileResult.cost_comparison.savings_pct > 0
                          ? `-${compileResult.cost_comparison.savings_pct}%`
                          : 'Optimal'}
                      </div>
                    </div>
                  </div>
                )}

                {/* Cost Model Component Breakdown */}
                {compileResult?.cost_breakdown?.components && (
                  <div>
                    <div className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider mb-2">
                      Cost Model Operator Breakdown
                    </div>
                    <div className="border border-zinc-800 rounded-xl overflow-hidden">
                      <table className="w-full text-left text-xs">
                        <thead className="bg-zinc-900 text-zinc-400 border-b border-zinc-800 font-mono text-[11px]">
                          <tr>
                            <th className="py-2.5 px-3">Component</th>
                            <th className="py-2.5 px-3">Estimated Cost</th>
                            <th className="py-2.5 px-3">Rationale & Notes</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-zinc-850 bg-zinc-950">
                          {Object.entries(compileResult.cost_breakdown.components).map(([k, comp]) => (
                            <tr key={k} className="hover:bg-zinc-900/40">
                              <td className="py-2 px-3 font-mono text-indigo-300 font-medium">{comp.name}</td>
                              <td className="py-2 px-3 font-mono text-zinc-200">{comp.value.toFixed(6)}</td>
                              <td className="py-2 px-3 text-zinc-400">{comp.notes}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Optimization Passes Trace */}
                <div>
                  <div className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider mb-2">
                    Optimization Passes Executed
                  </div>
                  <div className="space-y-2">
                    {compileResult?.optimization_trace && compileResult.optimization_trace.length > 0 ? (
                      compileResult.optimization_trace.map((tr, i) => (
                        <div
                          key={i}
                          className="flex items-center justify-between p-3 rounded-lg bg-zinc-900/60 border border-zinc-800 text-xs"
                        >
                          <div className="flex items-center gap-2">
                            <span className="w-2 h-2 rounded-full bg-indigo-500"></span>
                            <span className="font-semibold text-zinc-200">{tr.pass_name}</span>
                          </div>
                          <div className="flex items-center gap-2">
                            {tr.changes > 0 ? (
                              <span className="px-2 py-0.5 rounded bg-emerald-950 text-emerald-400 text-[11px] border border-emerald-800">
                                {tr.changes} changes applied
                              </span>
                            ) : (
                              <span className="px-2 py-0.5 rounded bg-zinc-800 text-zinc-500 text-[11px]">
                                No rewrite needed
                              </span>
                            )}
                          </div>
                        </div>
                      ))
                    ) : (
                      <div className="text-xs text-zinc-500 p-3 bg-zinc-900/40 rounded-lg">
                        Pass manager inactive or no passes registered.
                      </div>
                    )}
                  </div>
                </div>

                {/* Candidate Plans */}
                {compileResult?.candidates && compileResult.candidates.length > 0 && (
                  <div>
                    <div className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider mb-2">
                      Candidate Plans Evaluated
                    </div>
                    <div className="space-y-2">
                      {compileResult.candidates.map((cand, idx) => (
                        <div
                          key={idx}
                          className="p-3 rounded-lg bg-zinc-900 border border-zinc-800 text-xs flex items-center justify-between"
                        >
                          <div>
                            <div className="font-semibold text-zinc-200">{cand.rule}</div>
                            <div className="text-zinc-400 text-[11px] mt-0.5">{cand.rationale}</div>
                          </div>
                          <div className="font-mono text-indigo-400 font-medium">
                            Cost: {cand.cost_total.toFixed(6)}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* TAB 4: AST & Tokens */}
            {activeTab === 'ast' && (
              <div className="space-y-4">
                {/* Abstract Syntax Tree */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">
                      Abstract Syntax Tree (AST)
                    </span>
                    <span className="text-[10px] text-zinc-500 font-mono">Parsed Statement</span>
                  </div>
                  <pre className="p-4 rounded-xl bg-zinc-900/90 border border-zinc-800 text-xs font-mono text-zinc-300 leading-relaxed overflow-x-auto max-h-[350px]">
                    {JSON.stringify(compileResult?.ast, null, 2) || compileResult?.ast_repr || 'No AST generated'}
                  </pre>
                </div>

                {/* Lexer Tokens Table */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">
                      Lexer Token Stream ({compileResult?.tokens?.length || 0} tokens)
                    </span>
                  </div>
                  <div className="border border-zinc-800 rounded-xl overflow-hidden max-h-[300px] overflow-y-auto">
                    <table className="w-full text-left text-xs">
                      <thead className="bg-zinc-900 text-zinc-400 border-b border-zinc-800 font-mono text-[11px] sticky top-0">
                        <tr>
                          <th className="py-2 px-3">Type</th>
                          <th className="py-2 px-3">Lexeme</th>
                          <th className="py-2 px-3">Value</th>
                          <th className="py-2 px-3">Line:Col</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-zinc-850 bg-zinc-950 font-mono text-[11px]">
                        {compileResult?.tokens?.map((t, idx) => (
                          <tr key={idx} className="hover:bg-zinc-900/40">
                            <td className="py-1.5 px-3 text-indigo-400 font-medium">{t.type}</td>
                            <td className="py-1.5 px-3 text-emerald-300">{t.lexeme}</td>
                            <td className="py-1.5 px-3 text-zinc-400">{JSON.stringify(t.value)}</td>
                            <td className="py-1.5 px-3 text-zinc-500">{t.line}:{t.column}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            )}

            {/* TAB 5: Live Execution Simulation */}
            {activeTab === 'simulation' && (
              <div className="space-y-4">
                <div className="flex items-center justify-between p-3 rounded-lg bg-zinc-900 border border-zinc-800">
                  <div className="flex items-center gap-2">
                    <Table className="w-4 h-4 text-emerald-400" />
                    <span className="font-semibold text-xs text-zinc-200">
                      Query Execution Results ({executeResult?.is_simulation ? 'Simulated' : 'Native DB'})
                    </span>
                  </div>
                  <div className="flex items-center gap-3 text-xs font-mono text-zinc-400">
                    <span>Rows: {executeResult?.row_count || 0}</span>
                    <span>Elapsed: {executeResult?.elapsed_ms || 0} ms</span>
                  </div>
                </div>

                {executeResult?.error ? (
                  <div className="p-4 rounded-xl bg-rose-950/30 border border-rose-900 text-rose-300 text-xs">
                    <div className="font-semibold mb-1">Execution Error</div>
                    <div>{executeResult.error}</div>
                  </div>
                ) : executeResult?.rows && executeResult.rows.length > 0 ? (
                  <div className="border border-zinc-800 rounded-xl overflow-hidden shadow">
                    <table className="w-full text-left text-xs">
                      <thead className="bg-zinc-900 text-zinc-300 border-b border-zinc-800 font-mono text-[11px]">
                        <tr>
                          {executeResult.columns.map((col) => (
                            <th key={col} className="py-2.5 px-3 font-semibold">{col}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-zinc-850 bg-zinc-950 font-mono text-[11px]">
                        {executeResult.rows.map((row, rIdx) => (
                          <tr key={rIdx} className="hover:bg-zinc-900/50">
                            {executeResult.columns.map((col) => (
                              <td key={col} className="py-2 px-3 text-zinc-200">
                                {typeof row[col] === 'object' ? JSON.stringify(row[col]) : String(row[col] ?? 'NULL')}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="p-8 text-center text-zinc-500 border border-dashed border-zinc-800 rounded-xl">
                    <Play className="w-6 h-6 mx-auto mb-2 text-zinc-600" />
                    <p className="text-xs">Click "Simulate Run" above to evaluate query against sample dataset.</p>
                  </div>
                )}
              </div>
            )}

            {/* TAB 6: Schema JSON Editor */}
            {activeTab === 'schema' && (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-xs font-semibold text-zinc-300">Catalog Schema Definition</div>
                    <p className="text-[11px] text-zinc-500">
                      Edit collections, fields, and index attributes to observe optimizer behavior.
                    </p>
                  </div>
                  <button
                    onClick={() => {
                      setUseCustomSchema(true);
                      handleCompile();
                    }}
                    className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium"
                  >
                    Apply Schema
                  </button>
                </div>
                {schemaError && (
                  <div className="p-2.5 rounded bg-rose-950/40 border border-rose-900 text-rose-300 text-xs">
                    {schemaError}
                  </div>
                )}
                <textarea
                  value={customSchemaJson}
                  onChange={(e) => {
                    setCustomSchemaJson(e.target.value);
                    setUseCustomSchema(true);
                  }}
                  spellCheck="false"
                  className="w-full h-[450px] p-4 rounded-xl bg-zinc-900/90 border border-zinc-800 text-xs font-mono text-zinc-200 leading-relaxed focus:outline-none focus:border-indigo-500"
                />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

