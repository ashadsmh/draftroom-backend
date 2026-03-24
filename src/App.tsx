import React, { useState, useEffect, useRef } from 'react';
import { Search, TrendingUp, TrendingDown, Minus, Star, ChevronRight, Loader2, X } from 'lucide-react';
import { searchPlayers, getComputedAverages, NbaPlayer, getDraftRoomScore, DraftRoomScoreResponse, getTrajectory, TrajectoryResponse, getBatchScores } from './api/nba';

interface PlayerStats {
  pts: number;
  ast: number;
  reb: number;
}

interface Player {
  id: string;
  name: string;
  position: string;
  team: string;
  score: number | null;
  stats: PlayerStats | null;
  trend: 'up' | 'down' | 'stable' | null;
}

const getScoreColor = (score: number) => {
  if (score >= 70) return 'text-emerald-400';
  if (score >= 50) return 'text-amber-400';
  return 'text-rose-400';
};

const getScoreBg = (score: number) => {
  if (score >= 70) return 'bg-emerald-400/10 border-emerald-400/20';
  if (score >= 50) return 'bg-amber-400/10 border-amber-400/20';
  return 'bg-rose-400/10 border-rose-400/20';
};

const TrendIcon = ({ trend }: { trend: Player['trend'] }) => {
  if (!trend) return null;
  if (trend === 'up') return <TrendingUp className="w-5 h-5 text-emerald-400" />;
  if (trend === 'down') return <TrendingDown className="w-5 h-5 text-rose-400" />;
  return <Minus className="w-5 h-5 text-slate-400" />;
};

const PlayerCard = ({ player, isBreakout = false, onSelect }: { player: Player; isBreakout?: boolean; onSelect?: (player: Player) => void; key?: React.Key }) => {
  return (
    <div className={`relative flex flex-col p-6 rounded-2xl border transition-all duration-300 hover:-translate-y-1 ${
      isBreakout 
        ? 'bg-slate-900 border-amber-500/30 shadow-[0_0_20px_rgba(245,158,11,0.05)] hover:border-amber-500/50 hover:shadow-[0_0_25px_rgba(245,158,11,0.1)]' 
        : 'bg-slate-900 border-slate-800 hover:border-slate-700 hover:shadow-lg hover:shadow-slate-900/50'
    }`}>
      {isBreakout && (
        <div className="absolute -top-3 -right-3 bg-amber-500 text-slate-950 text-xs font-bold px-3 py-1 rounded-full flex items-center gap-1 shadow-lg">
          <Star className="w-3 h-3 fill-slate-950" />
          BREAKOUT
        </div>
      )}
      
      <div className="flex justify-between items-start mb-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <h3 className="text-lg font-bold text-slate-100">{player.name}</h3>
            <span className="text-xs font-semibold px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
              {player.position}
            </span>
          </div>
          <p className="text-sm text-slate-400">{player.team}</p>
        </div>
        <div className="flex flex-col items-end">
          <div className={`flex items-center justify-center w-12 h-12 rounded-xl border ${player.score ? getScoreBg(player.score) : 'bg-slate-800/50 border-slate-700'}`}>
            <span className={`text-xl font-bold ${player.score ? getScoreColor(player.score) : 'text-slate-500'}`}>{player.score ? player.score : '—'}</span>
          </div>
          <span className="text-[10px] font-medium text-slate-500 uppercase tracking-wider mt-1">DR Score</span>
        </div>
      </div>

      {player.stats && (
        <div className="grid grid-cols-3 gap-2 mb-5">
          <div className="bg-slate-950/50 rounded-lg p-3 border border-slate-800/50">
            <div className="text-xs text-slate-500 mb-1">PTS</div>
            <div className="text-lg font-semibold text-slate-200">{player.stats.pts.toFixed(1)}</div>
          </div>
          <div className="bg-slate-950/50 rounded-lg p-3 border border-slate-800/50">
            <div className="text-xs text-slate-500 mb-1">AST</div>
            <div className="text-lg font-semibold text-slate-200">{player.stats.ast.toFixed(1)}</div>
          </div>
          <div className="bg-slate-950/50 rounded-lg p-3 border border-slate-800/50">
            <div className="text-xs text-slate-500 mb-1">REB</div>
            <div className="text-lg font-semibold text-slate-200">{player.stats.reb.toFixed(1)}</div>
          </div>
        </div>
      )}

      <div className="mt-auto pt-4 border-t border-slate-800 flex items-center justify-between">
        <div className="flex items-center gap-2">
          {player.trend && (
            <>
              <TrendIcon trend={player.trend} />
              <span className="text-sm font-medium text-slate-400">
                {player.trend === 'up' ? 'Trending Up' : player.trend === 'down' ? 'Trending Down' : 'Holding Steady'}
              </span>
            </>
          )}
        </div>
        <button 
          onClick={() => onSelect && onSelect(player)}
          className="text-slate-400 hover:text-slate-200 transition-colors flex items-center gap-1 text-sm font-medium"
        >
          Load Analysis <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
};

export default function App() {
  const [searchQuery, setSearchQuery] = useState('');
  const [placeholder, setPlaceholder] = useState('');
  const [isFocused, setIsFocused] = useState(false);
  const typeState = useRef({ index: 0, text: '', isDeleting: false });

  const [searchResults, setSearchResults] = useState<NbaPlayer[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [selectedPlayer, setSelectedPlayer] = useState<NbaPlayer | null>(null);
  const [selectedPlayerStats, setSelectedPlayerStats] = useState<any>(null);
  const [isLoadingStats, setIsLoadingStats] = useState(false);
  const [selectedPlayerDraftScore, setSelectedPlayerDraftScore] = useState<DraftRoomScoreResponse | null>(null);
  const [isLoadingDraftScore, setIsLoadingDraftScore] = useState(false);
  const [selectedPlayerTrajectory, setSelectedPlayerTrajectory] = useState<TrajectoryResponse | null>(null);
  const [isLoadingTrajectory, setIsLoadingTrajectory] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  const [players, setPlayers] = useState<Player[]>([]);
  const [breakoutPlayers, setBreakoutPlayers] = useState<Player[]>([]);
  const [isLoadingBatch, setIsLoadingBatch] = useState(true);
  const [batchError, setBatchError] = useState<string | null>(null);

  const placeholders = [
    'Search LeBron James...',
    'Search Nikola Jokic...',
    'Search Jayson Tatum...',
    'Search Luka Doncic...'
  ];

  const handleSelectPlayerCard = (player: Player) => {
    const nameParts = player.name.split(' ');
    const firstName = nameParts[0];
    const lastName = nameParts.slice(1).join(' ');
    
    setSelectedPlayer({
      id: parseInt(player.id),
      first_name: firstName,
      last_name: lastName,
      position: player.position,
      team: { full_name: player.team }
    } as any);
    setSearchQuery(player.name);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  useEffect(() => {
    const HARDCODED_TOP_PROSPECTS: Player[] = [
      { id: '203999', name: 'Nikola Jokic', position: 'C', team: 'DEN', score: null, stats: null, trend: null },
      { id: '1628983', name: 'Shai Gilgeous-Alexander', position: 'PG', team: 'OKC', score: null, stats: null, trend: null },
      { id: '1641705', name: 'Victor Wembanyama', position: 'C', team: 'SAS', score: null, stats: null, trend: null },
      { id: '203507', name: 'Giannis Antetokounmpo', position: 'PF', team: 'MIL', score: null, stats: null, trend: null },
      { id: '1629029', name: 'Luka Doncic', position: 'PG', team: 'DAL', score: null, stats: null, trend: null },
      { id: '1630162', name: 'Anthony Edwards', position: 'SG', team: 'MIN', score: null, stats: null, trend: null }
    ];

    const HARDCODED_BREAKOUT_ALERTS: Player[] = [
      { id: '1630578', name: 'Scottie Barnes', position: 'PF', team: 'TOR', score: null, stats: null, trend: null },
      { id: '1630581', name: 'Jalen Johnson', position: 'PF', team: 'ATL', score: null, stats: null, trend: null },
      { id: '1630224', name: 'Evan Mobley', position: 'PF', team: 'CLE', score: null, stats: null, trend: null }
    ];

    setPlayers(HARDCODED_TOP_PROSPECTS);
    setBreakoutPlayers(HARDCODED_BREAKOUT_ALERTS);
    setIsLoadingBatch(false);
  }, []);

  useEffect(() => {
    if (isFocused) return;

    let timeout: NodeJS.Timeout;

    const type = () => {
      const state = typeState.current;
      const fullText = placeholders[state.index];

      if (state.isDeleting) {
        state.text = fullText.substring(0, state.text.length - 1);
      } else {
        state.text = fullText.substring(0, state.text.length + 1);
      }

      setPlaceholder(state.text);

      let typeSpeed = state.isDeleting ? 30 : 80;

      if (!state.isDeleting && state.text === fullText) {
        typeSpeed = 1500;
        state.isDeleting = true;
      } else if (state.isDeleting && state.text === '') {
        state.isDeleting = false;
        state.index = (state.index + 1) % placeholders.length;
        typeSpeed = 400;
      }

      timeout = setTimeout(type, typeSpeed);
    };

    timeout = setTimeout(type, 100);
    return () => clearTimeout(timeout);
  }, [isFocused]);

  useEffect(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    if (searchQuery.trim().length <= 2) {
      setSearchResults([]);
      return;
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    const timer = setTimeout(async () => {
      setIsSearching(true);
      setSearchError('');
      try {
        const results = await searchPlayers(searchQuery, controller.signal);
        setSearchResults(results);
      } catch (err: any) {
        if (err?.name === 'AbortError') {
          return;
        }
        const status = err?.response?.status || 'Unknown Status';
        console.error(`[searchPlayers] Failed with status ${status}:`, err);
        setSearchError(err instanceof Error ? err.message : 'An unknown error occurred');
        setSearchResults([]);
      } finally {
        if (abortControllerRef.current === controller) {
          setIsSearching(false);
        }
      }
    }, 300);

    return () => clearTimeout(timer);
  }, [searchQuery]);

  useEffect(() => {
    if (selectedPlayer) {
      setIsLoadingStats(true);
      setIsLoadingDraftScore(true);
      setIsLoadingTrajectory(true);
      
      getComputedAverages(selectedPlayer.id)
        .then(stats => {
          setSelectedPlayerStats(stats);
          const updateStats = (pList: Player[]) => pList.map(p => p.id === selectedPlayer.id.toString() ? { ...p, stats: { pts: stats.pts || 0, ast: stats.ast || 0, reb: stats.reb || 0 } } : p);
          setPlayers(updateStats);
          setBreakoutPlayers(updateStats);
        })
        .catch((err: any) => {
          const status = err?.response?.status || 'Unknown Status';
          console.error(`[getComputedAverages] Failed with status ${status}:`, err);
          setSelectedPlayerStats(null);
        })
        .finally(() => {
          setIsLoadingStats(false);
        });

      getDraftRoomScore(selectedPlayer.id)
        .then(score => {
          setSelectedPlayerDraftScore(score);
          const updateScore = (pList: Player[]) => pList.map(p => p.id === selectedPlayer.id.toString() ? { ...p, score: score.draftroom_score } : p);
          setPlayers(updateScore);
          setBreakoutPlayers(updateScore);
        })
        .catch((err: any) => {
          const status = err?.response?.status || 'Unknown Status';
          console.error(`[getDraftRoomScore] Failed with status ${status}:`, err);
          setSelectedPlayerDraftScore(null);
        })
        .finally(() => {
          setIsLoadingDraftScore(false);
        });

      getTrajectory(selectedPlayer.id)
        .then(traj => {
          setSelectedPlayerTrajectory(traj);
          const updateTrend = (pList: Player[]) => pList.map(p => p.id === selectedPlayer.id.toString() ? { ...p, trend: traj.DraftRoomScore.trend } : p);
          setPlayers(updateTrend);
          setBreakoutPlayers(updateTrend);
        })
        .catch((err: any) => {
          const status = err?.response?.status || 'Unknown Status';
          console.error(`[getTrajectory] Failed with status ${status}:`, err);
          setSelectedPlayerTrajectory(null);
        })
        .finally(() => {
          setIsLoadingTrajectory(false);
        });
    } else {
      setSelectedPlayerStats(null);
      setSelectedPlayerDraftScore(null);
      setSelectedPlayerTrajectory(null);
    }
  }, [selectedPlayer]);

  return (
    <div className="min-h-screen bg-slate-950 selection:bg-indigo-500/30">
      {/* Navigation */}
      <nav className="sticky top-0 z-50 bg-slate-950/80 backdrop-blur-md border-b border-slate-800">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <TrendingUp className="w-8 h-8 text-purple-500" strokeWidth={2.5} />
            <span className="text-xl font-extrabold tracking-tight text-white">DraftRoom</span>
          </div>
          <button className="px-5 py-2 border border-white text-white rounded-lg hover:bg-white/10 transition-colors text-sm font-medium">
            Login
          </button>
        </div>
      </nav>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        {/* Hero Section */}
        <div className="flex flex-col items-center text-center mb-16">
          <h1 className="text-5xl md:text-7xl lg:text-8xl font-extrabold text-slate-100 mb-4 tracking-tight flex justify-center">
            DraftRoom
          </h1>
          <p className="text-lg md:text-xl text-slate-400 italic max-w-2xl mb-10">
            Evaluate Talent with Precision
          </p>
          
          <div className="relative w-full max-w-2xl">
            <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
              <Search className="h-5 w-5 text-slate-500" />
            </div>
            <input
              type="text"
              className="block w-full pl-11 pr-12 py-4 bg-slate-900 border border-slate-800 rounded-2xl text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-purple-500/50 focus:border-purple-500 transition-all shadow-lg shadow-slate-900/50 text-lg"
              placeholder={placeholder}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onFocus={() => setIsFocused(true)}
              onBlur={() => {
                setIsFocused(false);
                setTimeout(() => setSearchResults([]), 200);
              }}
            />
            {isSearching && (
              <div className="absolute inset-y-0 right-0 pr-4 flex items-center pointer-events-none">
                <Loader2 className="h-5 w-5 text-purple-500 animate-spin" />
              </div>
            )}

            {/* Dropdown */}
            {searchResults.length > 0 && isFocused && (
              <div className="absolute top-full left-0 right-0 mt-2 bg-slate-900 border border-slate-800 rounded-xl shadow-2xl overflow-hidden z-50 max-h-80 overflow-y-auto">
                {searchResults.map((player) => (
                  <button
                    key={player.id}
                    className="w-full text-left px-4 py-3 hover:bg-slate-800 transition-colors flex items-center justify-between border-b border-slate-800/50 last:border-0"
                    onMouseDown={() => {
                      setSelectedPlayer(player);
                      setSearchQuery(`${player.first_name} ${player.last_name}`);
                      setSearchResults([]);
                    }}
                  >
                    <div>
                      <div className="text-slate-100 font-medium">
                        {player.first_name} {player.last_name}
                      </div>
                      <div className="text-sm text-slate-400">
                        {player.team.full_name} • {player.position || 'N/A'}
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            )}

            {/* Error Message */}
            {searchError && (
              <div className="absolute top-full left-0 right-0 mt-2 text-red-400 text-sm text-center">
                {searchError}
              </div>
            )}
          </div>
        </div>

        {/* Selected Player View */}
        {selectedPlayer && (
          <div className="mb-16">
            <div className="bg-slate-900 border border-slate-800 rounded-2xl p-8 relative overflow-hidden shadow-xl shadow-slate-900/50">
              <div className="absolute top-0 right-0 p-4">
                <button 
                  onClick={() => {
                    setSelectedPlayer(null);
                    setSearchQuery('');
                  }}
                  className="text-slate-500 hover:text-slate-300 transition-colors p-2 rounded-lg hover:bg-slate-800"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
              
              <div className="flex flex-col md:flex-row gap-8 items-start md:items-center">
                <div className="flex-1 w-full">
                  <div className="flex items-center gap-3 mb-2">
                    <h2 className="text-3xl font-bold text-slate-100">
                      {selectedPlayer.first_name} {selectedPlayer.last_name}
                    </h2>
                    <span className="px-3 py-1 rounded-full bg-slate-800 text-slate-300 text-sm font-semibold border border-slate-700">
                      {selectedPlayer.position || 'N/A'}
                    </span>
                    {selectedPlayerStats?.daysSinceLastGame > 7 && (
                      <span className="px-2 py-1 rounded bg-amber-500/10 text-amber-500 text-xs font-bold border border-amber-500/20 flex items-center gap-1">
                        Last played {selectedPlayerStats.daysSinceLastGame} days ago
                      </span>
                    )}
                  </div>
                  <p className="text-lg text-slate-400 mb-8">
                    {selectedPlayer.team.full_name}
                  </p>
                  
                  {isLoadingStats ? (
                    <div className="flex items-center gap-3 text-slate-400 py-4">
                      <Loader2 className="w-5 h-5 animate-spin text-purple-500" />
                      <span>Loading Season Form — Last 10 Games...</span>
                    </div>
                  ) : selectedPlayerStats ? (
                    <div>
                      <div className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
                        Season Form — Last {selectedPlayerStats.count} {selectedPlayerStats.count === 1 ? 'Game' : 'Games'}
                      </div>
                      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                        <div className="bg-slate-950/50 rounded-xl p-5 border border-slate-800/50">
                          <div className="text-sm text-slate-500 mb-1 font-medium">PTS</div>
                          <div className="text-3xl font-bold text-slate-200">{selectedPlayerStats.pts?.toFixed(1) || '0.0'}</div>
                        </div>
                        <div className="bg-slate-950/50 rounded-xl p-5 border border-slate-800/50">
                          <div className="text-sm text-slate-500 mb-1 font-medium">AST</div>
                          <div className="text-3xl font-bold text-slate-200">{selectedPlayerStats.ast?.toFixed(1) || '0.0'}</div>
                        </div>
                        <div className="bg-slate-950/50 rounded-xl p-5 border border-slate-800/50">
                          <div className="text-sm text-slate-500 mb-1 font-medium">REB</div>
                          <div className="text-3xl font-bold text-slate-200">{selectedPlayerStats.reb?.toFixed(1) || '0.0'}</div>
                        </div>
                        <div className="bg-slate-950/50 rounded-xl p-5 border border-slate-800/50">
                          <div className="text-sm text-slate-500 mb-1 font-medium">FG%</div>
                          <div className="text-3xl font-bold text-slate-200">{((selectedPlayerStats.fg_pct || 0) * 100).toFixed(1)}%</div>
                        </div>
                        <div className="bg-slate-950/50 rounded-xl p-5 border border-slate-800/50">
                          <div className="text-sm text-slate-500 mb-1 font-medium">STL</div>
                          <div className="text-3xl font-bold text-slate-200">{selectedPlayerStats.stl?.toFixed(1) || '0.0'}</div>
                        </div>
                        <div className="bg-slate-950/50 rounded-xl p-5 border border-slate-800/50">
                          <div className="text-sm text-slate-500 mb-1 font-medium">BLK</div>
                          <div className="text-3xl font-bold text-slate-200">{selectedPlayerStats.blk?.toFixed(1) || '0.0'}</div>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="text-slate-500 italic py-4 bg-slate-950/30 rounded-xl px-4 border border-slate-800/30">
                      No Season Form — Last 10 Games available.
                    </div>
                  )}

                  {/* 5-Game Projection Section */}
                  <div className="mt-8">
                    <div className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
                      5-Game Projection
                    </div>
                    {isLoadingTrajectory ? (
                      <div className="flex items-center gap-3 text-slate-400 py-4">
                        <Loader2 className="w-5 h-5 animate-spin text-purple-500" />
                        <span>Computing Projections...</span>
                      </div>
                    ) : selectedPlayerTrajectory ? (
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        {[
                          { label: 'PTS', data: selectedPlayerTrajectory.PTS },
                          { label: 'AST', data: selectedPlayerTrajectory.AST },
                          { label: 'REB', data: selectedPlayerTrajectory.REB },
                          { label: 'DR Score', data: selectedPlayerTrajectory.DraftRoomScore }
                        ].map((stat, idx) => (
                          <div key={idx} className="bg-slate-950/50 rounded-xl p-4 border border-slate-800/50 flex flex-col">
                            <div className="flex justify-between items-start mb-2">
                              <span className="text-sm text-slate-500 font-medium">{stat.label}</span>
                              {stat.data.trend === 'up' ? (
                                <TrendingUp className="w-4 h-4 text-emerald-400" />
                              ) : stat.data.trend === 'down' ? (
                                <TrendingDown className="w-4 h-4 text-rose-400" />
                              ) : (
                                <Minus className="w-4 h-4 text-slate-400" />
                              )}
                            </div>
                            <div className="text-2xl font-bold text-slate-200 mb-2">
                              {stat.data.value.toFixed(1)}
                            </div>
                            <div className="mt-auto">
                              <div className="flex items-center gap-1.5">
                                <div className="flex-1 h-1.5 bg-slate-800 rounded-full overflow-hidden">
                                  <div 
                                    className={`h-full rounded-full ${stat.data.confidence >= 70 ? 'bg-emerald-500' : stat.data.confidence >= 50 ? 'bg-amber-500' : 'bg-rose-500'}`}
                                    style={{ width: `${stat.data.confidence}%` }}
                                  />
                                </div>
                                <span className="text-[10px] font-bold text-slate-500">{stat.data.confidence.toFixed(0)}%</span>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="text-slate-500 italic py-4 bg-slate-950/30 rounded-xl px-4 border border-slate-800/30">
                        Projection unavailable (needs 5+ games).
                      </div>
                    )}
                  </div>
                </div>

                {/* DraftRoom Score Section */}
                <div className="w-full md:w-64 flex flex-col items-center bg-slate-950/50 p-6 rounded-2xl border border-slate-800/50">
                  {isLoadingDraftScore ? (
                    <div className="flex flex-col items-center justify-center py-8">
                      <Loader2 className="w-8 h-8 animate-spin text-purple-500 mb-4" />
                      <span className="text-sm text-slate-400">Computing Score...</span>
                    </div>
                  ) : selectedPlayerDraftScore ? (
                    <>
                      <div className={`flex items-center justify-center w-24 h-24 rounded-2xl border mb-3 ${getScoreBg(selectedPlayerDraftScore.draftroom_score)}`}>
                        <span className={`text-4xl font-bold ${getScoreColor(selectedPlayerDraftScore.draftroom_score)}`}>
                          {selectedPlayerDraftScore.draftroom_score}
                        </span>
                      </div>
                      <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-6">DraftRoom Score</span>
                      
                      <div className="w-full space-y-3">
                        <div className="flex justify-between items-center text-sm">
                          <span className="text-slate-500">TS Rel</span>
                          <span className="text-slate-200 font-medium">{selectedPlayerDraftScore.components.ts_rel_score}</span>
                        </div>
                        <div className="flex justify-between items-center text-sm">
                          <span className="text-slate-500">Playmaking</span>
                          <span className="text-slate-200 font-medium">{selectedPlayerDraftScore.components.play_score}</span>
                        </div>
                        <div className="flex justify-between items-center text-sm">
                          <span className="text-slate-500">Def Impact</span>
                          <span className="text-slate-200 font-medium">{selectedPlayerDraftScore.components.def_score}</span>
                        </div>
                        <div className="flex justify-between items-center text-sm">
                          <span className="text-slate-500">Foul Rate</span>
                          <span className="text-slate-200 font-medium">{selectedPlayerDraftScore.components.ftr_score}</span>
                        </div>
                        <div className="flex justify-between items-center text-sm">
                          <span className="text-slate-500">Vol Eff</span>
                          <span className="text-slate-200 font-medium">{selectedPlayerDraftScore.components.vol_eff_score}</span>
                        </div>
                      </div>
                    </>
                  ) : (
                    <div className="text-slate-500 italic py-8 text-center text-sm">
                      Score unavailable (needs 5+ games)
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Breakout Alerts */}
        <div className="mb-16">
          <div className="flex items-center gap-3 mb-6">
            <div className="p-2 bg-amber-500/10 rounded-lg border border-amber-500/20">
              <Star className="w-5 h-5 text-amber-500" />
            </div>
            <h2 className="text-2xl font-bold text-slate-100">Breakout Alerts</h2>
          </div>
          {isLoadingBatch ? (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              {[...Array(3)].map((_, i) => (
                <div key={i} className="bg-slate-900/50 rounded-2xl p-6 border border-slate-800/50 h-40 animate-pulse">
                  <div className="flex gap-4 h-full">
                    <div className="w-12 h-12 bg-slate-800 rounded-xl"></div>
                    <div className="flex-1 space-y-3 py-1">
                      <div className="h-4 bg-slate-800 rounded w-3/4"></div>
                      <div className="h-3 bg-slate-800 rounded w-1/2"></div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : batchError ? (
            <div className="text-rose-400 bg-rose-400/10 p-4 rounded-xl border border-rose-400/20">
              {batchError}
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {breakoutPlayers.map(player => (
                <PlayerCard key={player.id} player={player} isBreakout={true} onSelect={handleSelectPlayerCard} />
              ))}
            </div>
          )}
        </div>

        {/* Top Prospects Grid */}
        <div>
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-2xl font-bold text-slate-100">Top Prospects</h2>
            <div className="flex gap-2">
              <select className="bg-slate-900 border border-slate-800 text-slate-300 text-sm rounded-lg focus:ring-indigo-500 focus:border-indigo-500 block p-2 outline-none">
                <option>All Positions</option>
                <option>Guards</option>
                <option>Forwards</option>
                <option>Centers</option>
              </select>
              <select className="bg-slate-900 border border-slate-800 text-slate-300 text-sm rounded-lg focus:ring-indigo-500 focus:border-indigo-500 block p-2 outline-none">
                <option>Highest Score</option>
                <option>Trending Up</option>
                <option>Most Points</option>
              </select>
            </div>
          </div>
          {isLoadingBatch ? (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {[...Array(6)].map((_, i) => (
                <div key={i} className="bg-slate-900/50 rounded-2xl p-6 border border-slate-800/50 h-40 animate-pulse">
                  <div className="flex gap-4 h-full">
                    <div className="w-12 h-12 bg-slate-800 rounded-xl"></div>
                    <div className="flex-1 space-y-3 py-1">
                      <div className="h-4 bg-slate-800 rounded w-3/4"></div>
                      <div className="h-3 bg-slate-800 rounded w-1/2"></div>
                      <div className="h-8 bg-slate-800 rounded w-full mt-auto"></div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : batchError ? (
            <div className="text-rose-400 bg-rose-400/10 p-4 rounded-xl border border-rose-400/20">
              {batchError}
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {players.map(player => (
                <PlayerCard key={player.id} player={player} onSelect={handleSelectPlayerCard} />
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}