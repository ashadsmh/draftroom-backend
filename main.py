import math
import time
import logging
import asyncio
import requests
import unicodedata
from typing import List
from pydantic import BaseModel
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from nba_api.stats.static import players
from nba_api.stats.endpoints import commonplayerinfo, playergamelog
from nba_api.library.http import NBAHTTP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CURRENT_SEASON = "2025-26"

ALL_PLAYERS = players.get_players()
PLAYER_DICT = {p['id']: p for p in ALL_PLAYERS}

NBAHTTP.headers = {
    "Host": "stats.nba.com",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Connection": "keep-alive",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com"
}

app = FastAPI(
    title="DraftRoom API",
    description="NBA Analytics API using nba_api for rich player data and game logs.",
    version="1.0.0"
)

@app.on_event("startup")
async def startup_event():
    asyncio.get_event_loop().run_in_executor(None, get_batch_scores)

ALL_STAR_IDS = {2544, 115, 203999, 203507, 1629029, 1628983, 1630162, 1628369, 1626164, 1641705, 1630595, 1630169, 201142, 1628384, 1628973, 1630214, 203497, 1629627, 1628464, 1641784, 1630578, 1631094, 1630581, 1629675, 1628402}

BREAKOUT_CANDIDATE_IDS = [
    1630224, 1630578, 1631094, 1630581, 1630214, 1629628, 1628402, 1629675, 1631096, 1630559,
    1630162, 1630169, 1629029, 1628983, 1641705, 1628369, 1626164, 203999, 203507, 115
]

DEF_RTG = {
    "ATL": 118.4, "BOS": 110.5, "BKN": 115.4, "CHA": 119.2, "CHI": 115.7,
    "CLE": 112.1, "DAL": 114.6, "DEN": 112.3, "DET": 118.0, "GSW": 114.5,
    "HOU": 112.8, "IND": 117.6, "LAC": 114.6, "LAL": 114.8, "MEM": 113.7,
    "MIA": 111.5, "MIL": 113.1, "MIN": 108.4, "NOP": 111.9, "NYK": 112.0,
    "OKC": 111.0, "ORL": 110.8, "PHI": 113.8, "PHX": 113.7, "POR": 116.6,
    "SAC": 114.4, "SAS": 115.6, "TOR": 118.1, "UTA": 119.5, "WAS": 118.9
}

def normalize(val: float, min_val: float, max_val: float) -> float:
    return max(0.0, min(100.0, ((val - min_val) / (max_val - min_val)) * 100.0))

def parse_min(m: str | int | float) -> float:
    if isinstance(m, str) and ':' in m:
        pts = m.split(':')
        return float(pts[0]) + float(pts[1])/60.0
    elif m is not None:
        return float(m)
    return 1.0

def calculate_dr_score(pts: float, fga: float, fta: float, ast: float, tov: float, stl: float, blk: float, dreb: float) -> tuple[float, dict]:
    ts_denom = 2 * (fga + 0.44 * fta)
    ts = (pts / ts_denom) if ts_denom > 0 else 0
    ts_rel = ts - 0.56
    
    play = ast - (0.5 * tov)
    def_impact = stl + (0.7 * blk) + (0.3 * dreb)
    ftr = fta / max(fga, 1.0)
    vol_eff = ts_rel * math.sqrt(max(fga, 0))
    
    ts_rel_score = normalize(ts_rel, -0.08, 0.08)
    play_score = normalize(play, -1.0, 6.0)
    def_score = normalize(def_impact, 0.0, 4.5)
    ftr_score = normalize(ftr, 0.05, 0.45)
    vol_eff_score = normalize(vol_eff, -0.1, 0.3)
    
    raw_score = (
        ts_rel_score * 0.25 +
        play_score * 0.20 +
        def_score * 0.20 +
        ftr_score * 0.10 +
        vol_eff_score * 0.25
    )
    
    draftroom_score = min(99.9, 40.0 + (raw_score * 0.6))
    return draftroom_score, {
        "ts_rel_score": round(ts_rel_score, 1),
        "play_score": round(play_score, 1),
        "def_score": round(def_score, 1),
        "ftr_score": round(ftr_score, 1),
        "vol_eff_score": round(vol_eff_score, 1)
    }

def _compute_draftroom_score(player_id: int, season: str, games: list) -> dict:
    if len(games) < 5:
        raise ValueError("Not enough games to compute score (minimum 5).")
        
    recent_games = games[:10]
    games_sampled = len(recent_games)
    
    pts = sum(g.get('PTS') or 0 for g in recent_games) / games_sampled
    fga = sum(g.get('FGA') or 0 for g in recent_games) / games_sampled
    fta = sum(g.get('FTA') or 0 for g in recent_games) / games_sampled
    ast = sum(g.get('AST') or 0 for g in recent_games) / games_sampled
    tov = sum(g.get('TOV') or 0 for g in recent_games) / games_sampled
    stl = sum(g.get('STL') or 0 for g in recent_games) / games_sampled
    blk = sum(g.get('BLK') or 0 for g in recent_games) / games_sampled
    dreb = sum(g.get('DREB') or 0 for g in recent_games) / games_sampled
    
    draftroom_score, components = calculate_dr_score(pts, fga, fta, ast, tov, stl, blk, dreb)
    
    return {
        "player_id": player_id,
        "draftroom_score": round(draftroom_score, 1),
        "components": components,
        "games_sampled": games_sampled,
        "season": season
    }

def get_stat_array(recent_games: list, stat_name: str, is_pts: bool = False) -> list:
    arr = []
    for i, g in enumerate(recent_games):
        val = g.get(stat_name) or 0
        if is_pts:
            matchup = g.get('MATCHUP', '')
            opp = matchup[-3:] if matchup else ''
            opp_def = DEF_RTG.get(opp, 113.0)
            adj = opp_def / 113.0
            val = val / adj if adj > 0 else val
        arr.append(val)
    return arr

def compute_projection(stat_arr: list, mins: list, avg_min: float, n: int) -> tuple[float, str, float, list]:
    x_pms = [stat_arr[i] / mins[i] for i in range(n)]
    num = 0
    den = 0
    for i in range(n):
        w_i = 0.85 ** (n - 1 - i)
        num += w_i * x_pms[i]
        den += w_i
    x_hat = num / den if den > 0 else 0
    projected = x_hat * avg_min
    
    if n >= 10:
        avg_last3 = sum(x_pms[-3:]) / 3 * avg_min
        avg_prev7 = sum(x_pms[:-3]) / 7 * avg_min
    else:
        avg_last3 = sum(x_pms[-3:]) / len(x_pms[-3:]) * avg_min
        avg_prev7 = sum(x_pms[:-3]) / len(x_pms[:-3]) * avg_min if len(x_pms[:-3]) > 0 else avg_last3
        
    overall_avg = sum(x_pms) / n * avg_min
    delta = avg_last3 - avg_prev7
    
    if abs(delta) < 0.05 * overall_avg:
        trend = "stable"
    elif delta > 0:
        trend = "up"
    else:
        trend = "down"
        
    mean_pm = sum(x_pms) / n
    if mean_pm > 0:
        var = sum((x - mean_pm)**2 for x in x_pms) / n
        std = math.sqrt(var)
        cv = std / mean_pm
    else:
        cv = 0
    confidence = max(20.0, min(95.0, 100.0 - 120.0 * cv))
    
    return projected, trend, confidence, x_pms

def _compute_player_trajectory(player_id: int, season: str, games: list) -> dict:
    if len(games) < 5:
        raise ValueError("Not enough games for trajectory.")
        
    recent_games = games[:10]
    recent_games.reverse()
    n = len(recent_games)
    
    mins = [max(parse_min(g.get('MIN', 1)), 1.0) for g in recent_games]
    avg_min = sum(mins) / n
    
    pts_proj, pts_trend, pts_conf, pts_pms = compute_projection(get_stat_array(recent_games, 'PTS', True), mins, avg_min, n)
    ast_proj, ast_trend, ast_conf, ast_pms = compute_projection(get_stat_array(recent_games, 'AST'), mins, avg_min, n)
    reb_proj, reb_trend, reb_conf, reb_pms = compute_projection(get_stat_array(recent_games, 'REB'), mins, avg_min, n)
    
    fga_proj, _, _, fga_pms = compute_projection(get_stat_array(recent_games, 'FGA'), mins, avg_min, n)
    fta_proj, _, _, fta_pms = compute_projection(get_stat_array(recent_games, 'FTA'), mins, avg_min, n)
    tov_proj, _, _, tov_pms = compute_projection(get_stat_array(recent_games, 'TOV'), mins, avg_min, n)
    stl_proj, _, _, stl_pms = compute_projection(get_stat_array(recent_games, 'STL'), mins, avg_min, n)
    blk_proj, _, _, blk_pms = compute_projection(get_stat_array(recent_games, 'BLK'), mins, avg_min, n)
    dreb_proj, _, _, dreb_pms = compute_projection(get_stat_array(recent_games, 'DREB'), mins, avg_min, n)
    
    dr_proj, _ = calculate_dr_score(pts_proj, fga_proj, fta_proj, ast_proj, tov_proj, stl_proj, blk_proj, dreb_proj)
    
    dr_pms = []
    for i in range(n):
        dr_i, _ = calculate_dr_score(
            pts_pms[i]*mins[i], fga_pms[i]*mins[i], fta_pms[i]*mins[i],
            ast_pms[i]*mins[i], tov_pms[i]*mins[i], stl_pms[i]*mins[i],
            blk_pms[i]*mins[i], dreb_pms[i]*mins[i]
        )
        dr_pms.append(dr_i / mins[i])
        
    if n >= 10:
        avg_last3 = sum(dr_pms[-3:]) / 3 * avg_min
        avg_prev7 = sum(dr_pms[:-3]) / 7 * avg_min
    else:
        avg_last3 = sum(dr_pms[-3:]) / len(dr_pms[-3:]) * avg_min
        avg_prev7 = sum(dr_pms[:-3]) / len(dr_pms[:-3]) * avg_min if len(dr_pms[:-3]) > 0 else avg_last3
        
    overall_avg = sum(dr_pms) / n * avg_min
    delta = avg_last3 - avg_prev7
    if abs(delta) < 0.08 * overall_avg:
        dr_trend = "stable"
    elif delta > 0:
        dr_trend = "up"
    else:
        dr_trend = "down"
        
    mean_pm = sum(dr_pms) / n
    if mean_pm > 0:
        var = sum((x - mean_pm)**2 for x in dr_pms) / n
        std = math.sqrt(var)
        cv = std / mean_pm
    else:
        cv = 0
    dr_conf = max(20.0, min(95.0, 100.0 - 120.0 * cv))
    
    return {
        "PTS": {"value": round(pts_proj, 1), "trend": pts_trend, "confidence": round(pts_conf, 1)},
        "AST": {"value": round(ast_proj, 1), "trend": ast_trend, "confidence": round(ast_conf, 1)},
        "REB": {"value": round(reb_proj, 1), "trend": reb_trend, "confidence": round(reb_conf, 1)},
        "DraftRoomScore": {"value": round(dr_proj, 1), "trend": dr_trend, "confidence": round(dr_conf, 1)},
        "avg_minutes": round(avg_min, 1)
    }

_injury_cache: dict = {}
_injury_cache_reason: dict = {}
_injury_cache_type: dict = {}
_injury_cache_time: float = 0
INJURY_CACHE_TTL = 7200

# Configure CORS for the React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://draftroom-frontend.vercel.app",
        "https://*.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/players/search", summary="Search for NBA players by name")
def search_players(query: str = Query(..., min_length=1, description="Player name search query")) -> dict:
    """
    Searches the static nba_api player dictionary.
    Returns up to 20 matching players, prioritizing active players.
    """
    try:
        # Case-insensitive search
        matched = [p for p in ALL_PLAYERS if query.lower() in p['full_name'].lower()]
        
        # Sort active players to the top
        matched.sort(key=lambda x: not x.get('is_active', False))
        
        return {"data": matched[:20]}
    except Exception as e:
        logger.error(f"Error in search_players: {e}")
        raise HTTPException(status_code=500, detail=f"Error searching players: {str(e)}")

_batch_cache: dict = {}
_batch_cache_time: float = 0
BATCH_CACHE_TTL = 7200  # 2 hours

@app.get("/players/batch-scores", summary="Get batch scores for multiple players")
def get_batch_scores(player_ids: str = Query(None, description="Comma-separated player IDs"), season: str = Query(CURRENT_SEASON)) -> dict:
    global _batch_cache, _batch_cache_time
    now = time.time()
    if _batch_cache and (now - _batch_cache_time) < BATCH_CACHE_TTL:
        return _batch_cache  # instant return

    try:
        main_pool = [2544, 115, 203999, 203507, 1629029, 1628983, 1630162, 1628369, 1626164, 1641705, 1630595, 1630169]
        breakout_pool = BREAKOUT_CANDIDATE_IDS
        
        eval_pool = list(set(main_pool + breakout_pool))
        
        def fetch_data(pid):
            def _do_fetch():
                info = commonplayerinfo.CommonPlayerInfo(player_id=pid, timeout=8).get_normalized_dict()
                p_info = info.get('CommonPlayerInfo', [{}])[0]
                headline = info.get('PlayerHeadlineStats', [{}])[0]
                
                try:
                    gamelog = playergamelog.PlayerGameLog(player_id=pid, season=season, timeout=10)
                    data = gamelog.get_normalized_dict()
                    games = data.get("PlayerGameLog", [])
                except Exception:
                    games = []
                
                try:
                    dr_res = _compute_draftroom_score(player_id=pid, season=season, games=games)
                except Exception:
                    dr_res = None
                    
                try:
                    traj_res = _compute_player_trajectory(player_id=pid, season=season, games=games)
                except Exception:
                    traj_res = None
                    
                score = dr_res.get("draftroom_score", 0) if dr_res else 0
                projected_score = traj_res.get("DraftRoomScore", {}).get("value", 0) if traj_res else 0
                
                return {
                    "id": pid,
                    "name": p_info.get("DISPLAY_FIRST_LAST", "Unknown"),
                    "position": p_info.get("POSITION", ""),
                    "team": p_info.get("TEAM_ABBREVIATION", ""),
                    "score": score,
                    "projected_score": projected_score,
                    "trend": traj_res.get("DraftRoomScore", {}).get("trend", "stable") if traj_res else "stable",
                    "stats": {
                        "pts": headline.get("PTS", 0),
                        "ast": headline.get("AST", 0),
                        "reb": headline.get("REB", 0)
                    },
                    "delta": projected_score - score,
                    "avg_minutes": traj_res.get("avg_minutes", 0) if traj_res else 0
                }

            try:
                with ThreadPoolExecutor(max_workers=1) as ex:
                    return ex.submit(_do_fetch).result(timeout=8)
            except Exception as e:
                logger.error(f"Error fetching {pid}: {e}")
                return None
                
        results = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(fetch_data, pid): pid for pid in eval_pool}
            for future in as_completed(futures):
                res = future.result()
                if res:
                    results.append(res)
                    
        main_results = [r for r in results if r["id"] in main_pool]
        top_prospects = sorted(main_results, key=lambda x: x["score"], reverse=True)[:6]
        
        def filter_candidates(min_minutes, min_delta):
            logger.info(f"Starting filter with {len(results)} players")
            
            s1 = [r for r in results if r["id"] not in ALL_STAR_IDS]
            logger.info(f"After ALL_STAR_IDS filter: {len(s1)} players remain")
            
            s2 = []
            for r in s1:
                static_info = PLAYER_DICT.get(r["id"], {})
                career_games = static_info.get("career_games")
                if career_games is None or career_games < 400:
                    s2.append(r)
            logger.info(f"After career_games < 400 filter: {len(s2)} players remain")
            
            s3 = [r for r in s2 if r["stats"]["pts"] < 28]
            logger.info(f"After pts < 28 filter: {len(s3)} players remain")
            
            s4 = [r for r in s3 if r.get("avg_minutes", 0) >= min_minutes]
            logger.info(f"After avg_minutes >= {min_minutes} filter: {len(s4)} players remain")
            
            s5 = [r for r in s4 if r["delta"] >= min_delta]
            logger.info(f"After delta >= {min_delta} filter: {len(s5)} players remain")
            
            return s5
            
        breakout_candidates = filter_candidates(18, -2)
        
        def breakout_score(r):
            trend = r.get("trend", "stable")
            bonus = 2.0 if trend == "up" else (0.0 if trend == "stable" else -1.0)
            return r["delta"] + bonus
            
        breakout_alerts = sorted(breakout_candidates, key=breakout_score, reverse=True)[:3]
        
        if len(breakout_alerts) < 3:
            logger.warning("Fewer than 3 candidates passed relaxed filters. Using fallback.")
            fallback_candidates = [r for r in results if r["id"] not in ALL_STAR_IDS and r.get("avg_minutes", 0) >= 15]
            fallback_candidates = sorted(fallback_candidates, key=lambda x: x["score"], reverse=True)
            
            seen = {r["id"] for r in breakout_alerts}
            for r in fallback_candidates:
                if r["id"] not in seen:
                    breakout_alerts.append(r)
                    seen.add(r["id"])
                if len(breakout_alerts) >= 3:
                    break
        
        combined = {r["id"]: r for r in top_prospects + breakout_alerts}.values()
        
        result = {
            "data": list(combined),
            "top_prospects": top_prospects,
            "breakout_alerts": breakout_alerts
        }
        _batch_cache = result
        _batch_cache_time = now
        return result
    except Exception as e:
        logger.error(f"Error in get_batch_scores: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching batch scores: {str(e)}")

@app.get("/players/{player_id}/gamelog", summary="Get recent game logs for a player")
def get_player_gamelog(player_id: int, season: str = Query(CURRENT_SEASON, description="NBA Season format YYYY-YY")) -> dict:
    """
    Fetches game-by-game logs for the specified season.
    Defaults to the 2025-26 NBA season. Includes rich stats like PTS, AST, REB, FG%, STL, BLK, plus advanced metrics if available.
    """
    try:
        gamelog = playergamelog.PlayerGameLog(player_id=player_id, season=season, timeout=10)
        return gamelog.get_normalized_dict()
    except Exception as e:
        logger.error(f"Error in get_player_gamelog for {player_id}: {e}")
        raise HTTPException(status_code=422, detail=f"Error fetching game logs: {str(e)}")

@app.get("/players/{player_id}/draftroom-score", summary="Compute DraftRoom efficiency score")
def get_draftroom_score(player_id: int, season: str = Query(CURRENT_SEASON, description="NBA Season format YYYY-YY")) -> dict:
    """
    Computes a weighted efficiency score from 0-100 based on the last 10 games.
    """
    try:
        gamelog = playergamelog.PlayerGameLog(player_id=player_id, season=season, timeout=10)
        data = gamelog.get_normalized_dict()
        games = data.get("PlayerGameLog", [])
        
        if len(games) < 5:
            raise HTTPException(status_code=422, detail="Not enough games to compute score (minimum 5).")
            
        recent_games = games[:10]
        games_sampled = len(recent_games)
        
        # Aggregate stats
        pts = sum(g.get('PTS') or 0 for g in recent_games) / games_sampled
        fga = sum(g.get('FGA') or 0 for g in recent_games) / games_sampled
        fta = sum(g.get('FTA') or 0 for g in recent_games) / games_sampled
        ast = sum(g.get('AST') or 0 for g in recent_games) / games_sampled
        tov = sum(g.get('TOV') or 0 for g in recent_games) / games_sampled
        stl = sum(g.get('STL') or 0 for g in recent_games) / games_sampled
        blk = sum(g.get('BLK') or 0 for g in recent_games) / games_sampled
        dreb = sum(g.get('DREB') or 0 for g in recent_games) / games_sampled
        
        draftroom_score, components = calculate_dr_score(pts, fga, fta, ast, tov, stl, blk, dreb)
        
        return {
            "player_id": player_id,
            "draftroom_score": round(draftroom_score, 1),
            "components": components,
            "games_sampled": games_sampled,
            "season": season
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Error in get_draftroom_score for {player_id}: {e}")
        raise HTTPException(status_code=422, detail=f"Error computing DraftRoom score: {str(e)}")

@app.get("/players/{player_id}/dr-history", summary="Get per-game DR Score history")
def get_dr_history(player_id: int, games: str = Query("20", description="Number of games: 10, 20, 40, or season"), season: str = Query(CURRENT_SEASON)) -> dict:
    try:
        gamelog = playergamelog.PlayerGameLog(player_id=player_id, season=season, timeout=10)
        data = gamelog.get_normalized_dict()
        games_list = data.get("PlayerGameLog", [])
        
        if len(games_list) < 3:
            raise HTTPException(status_code=422, detail="Not enough games for history.")
            
        if games.lower() != "season":
            try:
                limit = int(games)
                games_list = games_list[:limit]
            except ValueError:
                pass
                
        games_list.reverse()
        
        history = []
        for i, g in enumerate(games_list):
            pts = g.get('PTS') or 0
            fga = g.get('FGA') or 0
            fta = g.get('FTA') or 0
            ast = g.get('AST') or 0
            tov = g.get('TOV') or 0
            stl = g.get('STL') or 0
            blk = g.get('BLK') or 0
            dreb = g.get('DREB') or 0
            
            draftroom_score, _ = calculate_dr_score(pts, fga, fta, ast, tov, stl, blk, dreb)
            
            matchup = g.get("MATCHUP", "")
            opponent = matchup[-3:] if matchup else ""
            
            history.append({
                "game_number": i + 1,
                "date": g.get("GAME_DATE", ""),
                "opponent": opponent,
                "dr_score": round(draftroom_score, 1),
                "pts": float(pts),
                "ast": float(ast),
                "reb": float(g.get("REB") or 0)
            })
            
        return history
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_dr_history for {player_id}: {e}")
        raise HTTPException(status_code=422, detail=f"Error computing DR history: {str(e)}")

@app.get("/players/{player_id}/trajectory", summary="Get 5-game projection")
def get_player_trajectory(player_id: int, season: str = Query(CURRENT_SEASON, description="NBA Season format YYYY-YY")) -> dict:
    try:
        gamelog = playergamelog.PlayerGameLog(player_id=player_id, season=season, timeout=10)
        data = gamelog.get_normalized_dict()
        games = data.get("PlayerGameLog", [])
        
        return _compute_player_trajectory(player_id, season, games)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Error in get_player_trajectory for {player_id}: {e}")
        raise HTTPException(status_code=422, detail=f"Error computing trajectory: {str(e)}")

def fetch_injury_report() -> dict:
    global _injury_cache, _injury_cache_reason, _injury_cache_type, _injury_cache_time
    now = time.time()
    if _injury_cache and (now - _injury_cache_time) < INJURY_CACHE_TTL:
        return _injury_cache
    try:
        url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        if resp.status_code != 200:
            return _injury_cache
        data = resp.json()
        new_cache, new_reason, new_type = {}, {}, {}
        for team in data.get("injuries", []):
            for injury in team.get("injuries", []):
                try:
                    athlete = injury.get("athlete", {})
                    full_name = str(athlete.get("displayName", "")).lower().strip()
                    key = ''.join(c for c in unicodedata.normalize('NFD', full_name) if unicodedata.category(c) != 'Mn')
                    raw_status = injury.get("status", "")
                    status = raw_status.get("type", raw_status.get("name", "")).strip() if isinstance(raw_status, dict) else str(raw_status).strip()
                    raw_type = injury.get("type", "")
                    injury_type = raw_type.get("name", "").lower().strip() if isinstance(raw_type, dict) else str(raw_type).lower().strip()
                    reason = str(injury.get("longComment", injury.get("shortComment", ""))).strip()
                    if key and status:
                        new_cache[key] = status
                        new_type[key] = injury_type
                        if reason:
                            new_reason[key] = reason
                except Exception:
                    continue
        _injury_cache, _injury_cache_reason, _injury_cache_type, _injury_cache_time = new_cache, new_reason, new_type, now
        return _injury_cache
    except Exception as e:
        logger.error(f"Injury fetch error: {e}")
        return _injury_cache

def get_injury_status(full_name: str) -> tuple[str, str, str] | None:
    fetch_injury_report()
    key = ''.join(c for c in unicodedata.normalize('NFD', full_name.lower().strip()) if unicodedata.category(c) != 'Mn')
    status = _injury_cache.get(key)
    if status:
        return (status, _injury_cache_reason.get(key, ""), _injury_cache_type.get(key, ""))
    return None

class LineupOptimizeRequest(BaseModel):
    player_names: List[str]
    season: str = CURRENT_SEASON

@app.post("/lineup/optimize", summary="Optimize a lineup of players")
def optimize_lineup(request: LineupOptimizeRequest) -> dict:
    resolved_players = []
    unresolved_names = []
    
    for name in request.player_names:
        name_lower = name.lower()
        
        # 1. Exact full_name match
        exact_match = next((p for p in ALL_PLAYERS if p['full_name'].lower() == name_lower), None)
        if exact_match:
            resolved_players.append({"name": name, "id": exact_match['id'], "full_name": exact_match['full_name']})
            continue
            
        # 2. All parts of the name are present in full_name
        parts = name_lower.split()
        all_parts_match = next((p for p in ALL_PLAYERS if all(part in p['full_name'].lower() for part in parts)), None)
        if all_parts_match:
            resolved_players.append({"name": name, "id": all_parts_match['id'], "full_name": all_parts_match['full_name']})
            continue
            
        # 3. Any part >3 chars matches
        long_parts = [part for part in parts if len(part) > 3]
        any_part_match = None
        if long_parts:
            any_part_match = next((p for p in ALL_PLAYERS if any(part in p['full_name'].lower() for part in long_parts)), None)
        if any_part_match:
            resolved_players.append({"name": name, "id": any_part_match['id'], "full_name": any_part_match['full_name']})
            continue
            
        unresolved_names.append(name)
        
    def process_player(player_info):
        pid = player_info['id']
        original_name = player_info['name']
        resolved_name = player_info['full_name']
        
        try:
            # Fetch CommonPlayerInfo
            info = commonplayerinfo.CommonPlayerInfo(player_id=pid, timeout=10).get_normalized_dict()
            p_info = info.get('CommonPlayerInfo', [{}])[0]
            position = p_info.get("POSITION", "")
            team_abbr = p_info.get("TEAM_ABBREVIATION", "")
            
            # Fetch PlayerGameLog
            gamelog = playergamelog.PlayerGameLog(player_id=pid, season=request.season, timeout=10)
            data = gamelog.get_normalized_dict()
            games_list = data.get("PlayerGameLog", [])
            
            if len(games_list) < 10:
                raise ValueError("Not enough games (minimum 10) to compute stats.")
                
            recent_10 = games_list[:10]
            
            # Compute DR Score using the EXACT same algorithm as get_draftroom_score
            pts = sum(g.get('PTS') or 0 for g in recent_10) / 10
            fga = sum(g.get('FGA') or 0 for g in recent_10) / 10
            fta = sum(g.get('FTA') or 0 for g in recent_10) / 10
            ast = sum(g.get('AST') or 0 for g in recent_10) / 10
            tov = sum(g.get('TOV') or 0 for g in recent_10) / 10
            stl = sum(g.get('STL') or 0 for g in recent_10) / 10
            blk = sum(g.get('BLK') or 0 for g in recent_10) / 10
            dreb = sum(g.get('DREB') or 0 for g in recent_10) / 10
            reb = sum(g.get('REB') or 0 for g in recent_10) / 10
            
            dr_score, _ = calculate_dr_score(pts, fga, fta, ast, tov, stl, blk, dreb)
            dr_score = round(dr_score, 1)
            
            # Minutes trend: avg last 3 games vs avg prior 7
            last_3 = recent_10[:3]
            prior_7 = recent_10[3:10]
            
            min_last_3 = sum(parse_min(g.get('MIN')) for g in last_3) / 3
            min_prior_7 = sum(parse_min(g.get('MIN')) for g in prior_7) / 7
            min_delta = min_last_3 - min_prior_7
            
            if min_delta >= 1.5:
                min_trend = "up"
            elif min_delta <= -1.5:
                min_trend = "down"
            else:
                min_trend = "stable"
                
            minutes_avg = sum(parse_min(g.get('MIN')) for g in recent_10) / 10
            
            # DR trend: per-game DR scores, last 3 vs prior 7 delta
            def calc_dr_for_game(g):
                g_pts = g.get('PTS') or 0
                g_fga = g.get('FGA') or 0
                g_fta = g.get('FTA') or 0
                g_ast = g.get('AST') or 0
                g_tov = g.get('TOV') or 0
                g_stl = g.get('STL') or 0
                g_blk = g.get('BLK') or 0
                g_dreb = g.get('DREB') or 0
                
                dr_score, _ = calculate_dr_score(g_pts, g_fga, g_fta, g_ast, g_tov, g_stl, g_blk, g_dreb)
                return dr_score
                
            dr_last_3 = sum(calc_dr_for_game(g) for g in last_3) / 3
            dr_prior_7 = sum(calc_dr_for_game(g) for g in prior_7) / 7
            dr_delta = dr_last_3 - dr_prior_7
            
            if dr_delta >= 2.0:
                dr_trend = "up"
            elif dr_delta <= -2.0:
                dr_trend = "down"
            else:
                dr_trend = "stable"
                
            # Most recent opponent abbreviation from MATCHUP field (last 3 chars)
            matchup_str = recent_10[0].get("MATCHUP", "")
            opp_abbr = matchup_str[-3:] if matchup_str else ""
            
            # Opponent DEF_RTG rank using the existing DEF_RTG dict
            # rank all 30 teams, rank 1 = easiest (highest DEF_RTG), rank 30 = hardest
            sorted_def_rtg = sorted(DEF_RTG.items(), key=lambda x: x[1], reverse=True)
            opp_rank = next((i + 1 for i, (team, _) in enumerate(sorted_def_rtg) if team == opp_abbr), 15)
            opp_def_rating = DEF_RTG.get(opp_abbr, 114.0)
            
            # Matchup label
            if opp_rank <= 5:
                matchup_label = f"vs {opp_abbr} (bottom-5 defense 🔥)"
            elif opp_rank <= 10:
                matchup_label = f"vs {opp_abbr} (weak defense)"
            elif opp_rank >= 26:
                matchup_label = f"vs {opp_abbr} (top-5 defense ⚠️)"
            elif opp_rank >= 21:
                matchup_label = f"vs {opp_abbr} (tough defense)"
            else:
                matchup_label = f"vs {opp_abbr} (neutral matchup)"
                
            matchup_boost = opp_rank <= 10
            
            # Compute a start_score for ranking
            start_score = dr_score
            if matchup_boost:
                start_score += 4
            if opp_rank >= 20:
                start_score -= 3
            if min_trend == "up":
                start_score += 2
            elif min_trend == "down":
                start_score -= 3

            injury_info = get_injury_status(resolved_name)
            injury_status = injury_info[0] if injury_info else None
            injury_reason = injury_info[1] if injury_info else ""

            # Build reasoning bullets
            reasons = []

            if injury_status:
                status_lower = injury_status.lower()
                short = injury_reason.split(";")[0].replace("Injury/Illness - ", "").strip() if injury_reason else ""
                if any(k in status_lower for k in ["out", "inactive", "suspension", "suspended"]):
                    reasons.insert(0, f"OUT{' — ' + short if short else ''} ⚠️")
                    start_score -= 50
                elif any(k in status_lower for k in ["questionable", "doubtful"]):
                    reasons.insert(0, f"QUESTIONABLE{' — ' + short if short else ''} ⚠️")
                    start_score -= 10

            if dr_score >= 75:
                reasons.append(f"Elite DR Score of {dr_score}")
            elif dr_score >= 65:
                reasons.append(f"Strong DR Score of {dr_score}")
            else:
                reasons.append(f"DR Score of {dr_score}")
                
            if abs(dr_delta) >= 3:
                if dr_delta > 0:
                    reasons.append(f"+{round(dr_delta, 1)} DR over last 3 games — trending up")
                else:
                    reasons.append(f"{round(dr_delta, 1)} DR over last 3 games — trending down")
                    
            if abs(min_delta) >= 2 and min_trend == "up":
                reasons.append(f"Minutes up {round(min_delta, 1)} mpg recently — increased role")
            elif abs(min_delta) >= 2 and min_trend == "down":
                reasons.append(f"Minutes down {round(abs(min_delta), 1)} mpg — reduced role, monitor")
                
            reasons.append(matchup_label)
            
            if pts >= 25:
                reasons.append(f"Averaging {round(pts, 1)} PPG over last 10")
            elif pts >= 18:
                reasons.append(f"{round(pts, 1)} PPG over last 10 games")
                
            if ast >= 7:
                reasons.append(f"High playmaking: {round(ast, 1)} APG")
                
            if stl + blk >= 2.5:
                reasons.append(f"Defensive upside: {round(stl, 1)} STL + {round(blk, 1)} BLK")
                
            # Assign tier
            if start_score >= 75:
                tier = "Lock In"
            elif start_score >= 65:
                tier = "Start"
            elif start_score >= 55:
                tier = "Monitor"
            else:
                tier = "Sit"
                
            return {
                "id": pid,
                "name": resolved_name,
                "position": position,
                "team": team_abbr,
                "dr_score": dr_score,
                "dr_trend": dr_trend,
                "dr_delta": round(dr_delta, 1),
                "minutes_avg": round(minutes_avg, 1),
                "minutes_trend": min_trend,
                "minutes_delta": round(min_delta, 1),
                "opp_abbr": opp_abbr,
                "opp_def_rating": opp_def_rating,
                "opp_rank": opp_rank,
                "matchup_label": matchup_label,
                "matchup_boost": matchup_boost,
                "start_score": round(start_score, 1),
                "tier": tier,
                "reasons": reasons,
                "injury_status": injury_status,
                "stats": {
                    "pts": round(pts, 1),
                    "ast": round(ast, 1),
                    "reb": round(reb, 1),
                    "stl": round(stl, 1),
                    "blk": round(blk, 1)
                },
                "recommended_start": False,
                "error": None
            }
        except Exception as e:
            return {
                "id": pid,
                "name": original_name,
                "error": str(e)
            }
            
    valid_players = []
    errored_players = []
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(process_player, p): p for p in resolved_players}
        for future in as_completed(futures):
            res = future.result()
            if res.get("error"):
                errored_players.append(res)
            else:
                valid_players.append(res)
                
    # Sort all valid players by start_score descending
    valid_players.sort(key=lambda x: x["start_score"], reverse=True)
    
    # Mark recommended_start: True for top 5 players
    for i, p in enumerate(valid_players):
        if i < 5:
            p["recommended_start"] = True
            
    return {
        "players": valid_players,
        "unresolved_names": unresolved_names,
        "errored_players": errored_players,
        "total_resolved": len(resolved_players),
        "roster_size": len(request.player_names)
    }

@app.get("/players/{player_id}", summary="Get comprehensive individual player info")
def get_player_info(player_id: int) -> dict:
    """
    Fetches detailed player info including height, weight, draft info, 
    current team, and headline stats (PTS, AST, REB averages).
    """
    try:
        info = commonplayerinfo.CommonPlayerInfo(player_id=player_id, timeout=10)
        # get_normalized_dict() converts the pandas DataFrames to standard Python dicts
        return info.get_normalized_dict()
    except Exception as e:
        logger.error(f"Error in get_player_info for {player_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching player info: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)