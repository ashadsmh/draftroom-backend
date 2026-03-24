export interface NbaPlayer {
  id: number;
  full_name: string;
  first_name: string;
  last_name: string;
  is_active: boolean;
  // Added to prevent UI crashes in App.tsx which expects these fields
  position?: string;
  team: {
    full_name: string;
  };
}

const BASE_URL = 'http://localhost:8000';

const handleResponse = async (response: Response) => {
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`);
  }
  return response.json();
};

export const searchPlayers = async (query: string, signal?: AbortSignal): Promise<NbaPlayer[]> => {
  if (!query) return [];
  const response = await fetch(`${BASE_URL}/players/search?query=${encodeURIComponent(query)}`, {
    signal,
  });
  const data = await handleResponse(response);
  
  // Map the backend search response to satisfy the NbaPlayer interface
  // and prevent UI crashes since the new backend search doesn't return team/position
  return (data?.data ?? []).map((player: any) => ({
    id: player.id,
    full_name: player.full_name,
    first_name: player.first_name,
    last_name: player.last_name,
    is_active: player.is_active,
    position: 'N/A',
    team: {
      full_name: 'NBA'
    }
  }));
};

/**
 * @deprecated The BallDontLie free tier does not support the season averages endpoint.
 * Use getComputedAverages instead.
 */
export const getSeasonAverages = async (playerId: number) => {
  return null;
};

export interface ComputedStats {
  pts: number;
  ast: number;
  reb: number;
  fg_pct: number;
  stl: number;
  blk: number;
  count: number;
  daysSinceLastGame?: number;
}

export const getComputedAverages = async (playerId: number): Promise<ComputedStats | null> => {
  try {
    const games = await getRecentGames(playerId);
    if (!games || games.length === 0) return null;

    const recentGames = games.slice(0, 10);
    const count = recentGames.length;

    let pts = 0, ast = 0, reb = 0, fg_pct = 0, stl = 0, blk = 0;

    recentGames.forEach((game: any) => {
      pts += game.PTS || 0;
      ast += game.AST || 0;
      reb += game.REB || 0;
      fg_pct += game.FG_PCT || 0;
      stl += game.STL || 0;
      blk += game.BLK || 0;
    });

    let daysSinceLastGame: number | undefined;
    if (recentGames[0] && recentGames[0].GAME_DATE) {
      const gameDate = new Date(recentGames[0].GAME_DATE);
      const today = new Date();
      const diffTime = today.getTime() - gameDate.getTime();
      const diffDays = Math.floor(diffTime / (1000 * 60 * 60 * 24));
      daysSinceLastGame = diffDays > 0 ? diffDays : 0;
    }

    return {
      pts: Number((pts / count).toFixed(1)),
      ast: Number((ast / count).toFixed(1)),
      reb: Number((reb / count).toFixed(1)),
      fg_pct: Number((fg_pct / count).toFixed(1)),
      stl: Number((stl / count).toFixed(1)),
      blk: Number((blk / count).toFixed(1)),
      count,
      daysSinceLastGame,
    };
  } catch (error) {
    return null;
  }
};

export const getRecentGames = async (playerId: number) => {
  const response = await fetch(`${BASE_URL}/players/${playerId}/gamelog`);
  const data = await handleResponse(response);
  return data?.PlayerGameLog ?? [];
};

export interface DraftRoomScoreResponse {
  player_id: number;
  draftroom_score: number;
  components: {
    ts_rel_score: number;
    play_score: number;
    def_score: number;
    ftr_score: number;
    vol_eff_score: number;
  };
  games_sampled: number;
  season: string;
}

export interface DraftRoomScoreResponse {
  player_id: number;
  draftroom_score: number;
  components: {
    ts_rel_score: number;
    play_score: number;
    def_score: number;
    ftr_score: number;
    vol_eff_score: number;
  };
  games_sampled: number;
  season: string;
}

export const getDraftRoomScore = async (playerId: number): Promise<DraftRoomScoreResponse | null> => {
  try {
    const response = await fetch(`${BASE_URL}/players/${playerId}/draftroom-score`);
    if (!response.ok) return null;
    return await response.json();
  } catch (error) {
    return null;
  }
};

export interface TrajectoryStat {
  value: number;
  trend: 'up' | 'down' | 'stable';
  confidence: number;
}

export interface TrajectoryResponse {
  PTS: TrajectoryStat;
  AST: TrajectoryStat;
  REB: TrajectoryStat;
  DraftRoomScore: TrajectoryStat;
}

export const getTrajectory = async (playerId: number): Promise<TrajectoryResponse | null> => {
  try {
    const response = await fetch(`${BASE_URL}/players/${playerId}/trajectory`);
    if (!response.ok) return null;
    return await response.json();
  } catch (error) {
    return null;
  }
};

export interface BatchPlayer {
  id: number;
  name: string;
  position: string;
  team: string;
  score: number;
  projected_score: number;
  trend: 'up' | 'down' | 'stable';
  stats: {
    pts: number;
    ast: number;
    reb: number;
  };
}

export const getBatchScores = async (playerIds: number[]): Promise<BatchPlayer[]> => {
  try {
    const response = await fetch(`${BASE_URL}/players/batch-scores?player_ids=${playerIds.join(',')}`);
    if (!response.ok) return [];
    const data = await response.json();
    return data.data || [];
  } catch (error) {
    console.error("Failed to fetch batch scores", error);
    return [];
  }
};
