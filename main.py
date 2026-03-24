import math
import time
import unicodedata
import requests
from datetime import datetime
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from nba_api.stats.static import players
from nba_api.stats.endpoints import commonplayerinfo, playergamelog
from nba_api.library.http import NBAHTTP

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

ALL_STAR_IDS = {2544, 115, 203999, 203507, 1629029, 1628983, 1630162, 1628369, 1626164, 1641705, 1630595, 1630169, 201142, 1628384, 1628973, 1630214, 203497, 1629627, 1628464, 1641784, 1630578, 1631094, 1630581, 1629675, 1628402}

DEF_RTG = {
    "ATL": 118.4, "BOS": 110.5, "BKN": 115.4, "CHA": 119.2, "CHI": 115.7,
    "CLE": 112.1, "DAL": 114.6, "DEN": 112.3, "DET": 118.0, "GSW": 114.5,
    "HOU": 112.8, "IND": 117.6, "LAC": 114.6, "LAL": 114.8, "MEM": 113.7,
    "MIA": 111.5, "MIL": 113.1, "MIN": 108.4, "NOP": 111.9, "NYK": 112.0,
    "OKC": 111.0, "ORL": 110.8, "PHI": 113.8, "PHX": 113.7, "POR": 116.6,
    "SAC": 114.4, "SAS": 115.6, "TOR": 118.1, "UTA": 119.5, "WAS": 118.9
}

POSITION_ORDER = {
    "PG": 0, "SG": 1, "SF": 2, "PF": 3, "C": 4,
    "G": 1, "F": 2, "G-F": 1, "F-G": 2, "F-C": 3, "C-F": 3
}

# Position-based defensive baseline — guards get a floor since they
# naturally accumulate fewer blocks/rebounds than bigs
POSITION_DEFENSE_BASELINE = {
    "PG": 0.8, "SG": 0.7, "G": 0.75,
    "SF": 0.5, "F": 0.5, "G-F": 0.6, "F-G": 0.6,
    "PF": 0.3, "F-C": 0.2, "C-F": 0.2,
    "C": 0.0
}

# DR Score weights — must be identical across all endpoints
DR_WEIGHTS = {
    "ts_rel":  0.25,
    "vol_eff": 0.25,
    "play":    0.20,
    "defense": 0.25,
    "ftr":     0.05,
}

# Normalization ranges
DR_RANGES = {
    "ts_rel":  (-0.08, 0.08),
    "vol_eff": (-0.1, 0.3),
    "play":    (-1.0, 6.0),
    "defense": (0.0, 7.0),
    "ftr":     (0.05, 0.45),
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://draftroom-frontend.vercel.app",
        "https://draftroom-co.vercel.app",
        "https://*.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Injury / Suspension cache ────────────────────────────────────────────────

_injury_cache: Dict[str, str] = {}
_injury_cache_reason: Dict[str, str] = {}
_injury_cache_type: Dict[str, str] = {}
_injury_cache_time: float = 0
INJURY_CACHE_TTL = 7200  # 2 hours


def fetch_injury_report() -> Dict[str, str]:
    global _injury_cache, _injury_cache_reason, _injury_cache_type, _injury_cache_time

    now = time.time()
    if _injury_cache and (now - _injury_cache_time) < INJURY_CACHE_TTL:
        return _injury_cache

    try:
        url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
        resp = requests.get(url, timeout=8, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json"
        })
        if resp.status_code != 200:
            print(f"Injury fetch failed: {resp.status_code}")
            return _injury_cache

        data = resp.json()
        new_cache: Dict[str, str] = {}
        new_reason: Dict[str, str] = {}
        new_type: Dict[str, str] = {}

        for team in data.get("injuries", []):
            for injury in team.get("injuries", []):
                try:
                    athlete = injury.get("athlete", {})
                    full_name = athlete.get("displayName", "")
                    if isinstance(full_name, dict):
                        full_name = full_name.get("value", "")
                    full_name = str(full_name).lower().strip()

                    raw_status = injury.get("status", "")
                    if isinstance(raw_status, dict):
                        status = raw_status.get("type", raw_status.get("name", "")).strip()
                    else:
                        status = str(raw_status).strip()

                    raw_type = injury.get("type", "")
                    if isinstance(raw_type, dict):
                        injury_type = raw_type.get("name", "").lower().strip()
                    else:
                        injury_type = str(raw_type).lower().strip()

                    reason = injury.get("longComment", injury.get("shortComment", ""))
                    if isinstance(reason, dict):
                        reason = reason.get("value", "")
                    reason = str(reason).strip()

                    if full_name and status:
                        key = strip_accents(full_name)
                        new_cache[key] = status
                        new_type[key] = injury_type
                        if reason:
                            new_reason[key] = reason

                except Exception as field_err:
                    print(f"Injury field error: {field_err} | keys: {list(injury.keys())} | {str(injury)[:200]}")
                    continue

        _injury_cache = new_cache
        _injury_cache_reason = new_reason
        _injury_cache_type = new_type
        _injury_cache_time = now
        print(f"Injury cache refreshed: {len(new_cache)} players")
        return _injury_cache

    except Exception as e:
        print(f"Injury fetch error: {e}")
        return _injury_cache


def get_injury_status(full_name: str) -> Optional[tuple]:
    """Returns (status, reason, type) or None if not listed."""
    fetch_injury_report()
    key = strip_accents(full_name.lower().strip())
    status = _injury_cache.get(key)
    reason = _injury_cache_reason.get(key, "")
    injury_type = _injury_cache_type.get(key, "")
    if status:
        return (status, reason, injury_type)
    return None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def strip_accents(s: str) -> str:
    return ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )


def is_suspension(status: str, injury_type: str) -> bool:
    combined = (status + " " + injury_type).lower()
    return any(k in combined for k in ["suspension", "suspended", "disciplinary"])


def is_unavailable(status: str, injury_type: str) -> bool:
    if is_suspension(status, injury_type):
        return True
    return any(k in status.lower() for k in ["out", "inactive"])


def is_questionable(status: str) -> bool:
    return any(k in status.lower() for k in ["questionable", "doubtful"])


def normalize(val: float, min_val: float, max_val: float) -> float:
    return max(0.0, min(100.0, ((val - min_val) / (max_val - min_val)) * 100.0))


def compute_def_impact(stl: float, blk: float, dreb: float, position: str = "") -> float:
    """
    Defensive impact formula:
    - STL x2.2 (most position-agnostic, rewards perimeter defenders)
    - BLK x1.4 (rim protection, naturally rewards Wemby-type bigs)
    - DREB x0.25 capped at 1.5 (prevents pure rebounders from dominating)
    - Position baseline gives guards a floor to compensate for lower box score counts
    """
    baseline = POSITION_DEFENSE_BASELINE.get(position, 0.4)
    dreb_contribution = min(dreb * 0.25, 1.5)
    return (stl * 2.2) + (blk * 1.4) + dreb_contribution + baseline


def compute_dr_score(pts: float, fga: float, fta: float, ast: float, tov: float,
                     stl: float, blk: float, dreb: float, position: str = "") -> dict:
    """
    Single source of truth for DR Score computation.
    Used by all endpoints to ensure full consistency.
    """
    ts_denom = 2 * (fga + 0.44 * fta)
    ts = (pts / ts_denom) if ts_denom > 0 else 0
    ts_rel = ts - 0.56
    play = ast - (0.5 * tov)
    def_impact = compute_def_impact(stl, blk, dreb, position)
    ftr = fta / max(fga, 1.0)
    vol_eff = ts_rel * math.sqrt(fga)

    ts_rel_score  = normalize(ts_rel,    *DR_RANGES["ts_rel"])
    vol_eff_score = normalize(vol_eff,   *DR_RANGES["vol_eff"])
    play_score    = normalize(play,      *DR_RANGES["play"])
    def_score     = normalize(def_impact, *DR_RANGES["defense"])
    ftr_score     = normalize(ftr,       *DR_RANGES["ftr"])

    raw_score = (
        ts_rel_score  * DR_WEIGHTS["ts_rel"]  +
        vol_eff_score * DR_WEIGHTS["vol_eff"] +
        play_score    * DR_WEIGHTS["play"]    +
        def_score     * DR_WEIGHTS["defense"] +
        ftr_score     * DR_WEIGHTS["ftr"]
    )

    draftroom_score = min(99.9, 40.0 + (raw_score * 0.6))

    return {
        "draftroom_score": round(draftroom_score, 1),
        "components": {
            "ts_rel_score":  round(ts_rel_score, 1),
            "play_score":    round(play_score, 1),
            "def_score":     round(def_score, 1),
            "ftr_score":     round(ftr_score, 1),
            "vol_eff_score": round(vol_eff_score, 1),
        }
    }


# ─── Nickname map ─────────────────────────────────────────────────────────────

NICKNAME_MAP = {
    "wemby": "victor wembanyama",
    "joker": "nikola jokic",
    "jokic": "nikola jokic",
    "curry": "stephen curry",
    "steph": "stephen curry",
    "sga": "shai gilgeous-alexander",
    "shai": "shai gilgeous-alexander",
    "bron": "lebron james",
    "lebron": "lebron james",
    "kd": "kevin durant",
    "ant": "anthony edwards",
    "ant man": "anthony edwards",
    "giannis": "giannis antetokounmpo",
    "greek freak": "giannis antetokounmpo",
    "luka": "luka doncic",
    "doncic": "luka doncic",
    "trae": "trae young",
    "dame": "damian lillard",
    "book": "devin booker",
    "devin": "devin booker",
    "jaylen": "jaylen brown",
    "jt": "jayson tatum",
    "tatum": "jayson tatum",
    "embiid": "joel embiid",
    "jojo": "joel embiid",
    "ad": "anthony davis",
    "cp3": "chris paul",
    "pg": "paul george",
    "pg13": "paul george",
    "russ": "russell westbrook",
    "harden": "james harden",
    "kawhi": "kawhi leonard",
    "payton": "payton prichard",
    "pritchard": "payton prichard",
    "cam": "cam thomas",
    "victor": "victor wembanyama",
    "cade": "cade cunningham",
    "scoot": "scoot henderson",
    "evan": "evan mobley",
    "mobley": "evan mobley",
    "franz": "franz wagner",
    "wagner": "franz wagner",
    "bam": "bam adebayo",
    "tyrese": "tyrese haliburton",
    "hali": "tyrese haliburton",
    "herro": "tyler herro",
    "klay": "klay thompson",
    "draymond": "draymond green",
    "jalen": "jalen brunson",
    "brunson": "jalen brunson",
    "randle": "julius randle",
    "donovan": "donovan mitchell",
    "spida": "donovan mitchell",
    "zion": "zion williamson",
    "ja": "ja morant",
    "morant": "ja morant",
    "scottie": "scottie barnes",
    "barnes": "scottie barnes",
    "garland": "darius garland",
    "lauri": "lauri markkanen",
    "rudy": "rudy gobert",
    "gobert": "rudy gobert",
    "kat": "karl-anthony towns",
    "towns": "karl-anthony towns",
    "melo": "carmelo anthony",
    "cp": "chris paul",
    "dlo": "d'angelo russell",
    "dinwiddie": "spencer dinwiddie",
    "obi": "obi toppin",
    "naz": "naz reid",
    "herb": "herb jones",
    "ivey": "jaden ivey",
    "kuminga": "jonathan kuminga",
    "jk": "jonathan kuminga",
    "wiggins": "andrew wiggins",
    "wiggs": "andrew wiggins",
    "poole": "jordan poole",
    "sabonis": "domantas sabonis",
    "domas": "domantas sabonis",
    "fox": "de'aaron fox",
    "dejounte": "dejounte murray",
    "murray": "dejounte murray",
    "middleton": "khris middleton",
    "khris": "khris middleton",
    "jrue": "jrue holiday",
    "holiday": "jrue holiday",
    "brogdon": "malcolm brogdon",
    "siakam": "pascal siakam",
    "pascal": "pascal siakam",
    "oladipo": "victor oladipo",
    "vuc": "nikola vucevic",
    "vucevic": "nikola vucevic",
}


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/players/search")
def search_players(query: str = Query(..., min_length=1)):
    try:
        all_players = players.get_players()
        query_stripped = strip_accents(query.lower())
        matched = [
            p for p in all_players
            if query_stripped in strip_accents(p['full_name'].lower())
        ]
        matched.sort(key=lambda x: not x.get('is_active', False))
        return {"data": matched[:20]}
    except Exception as e:
        print(f"Error in search_players: {e}")
        raise HTTPException(status_code=500, detail=f"Error searching players: {str(e)}")


@app.get("/players/batch-scores")
def get_batch_scores(player_ids: str = Query(None), season: str = Query("2025-26")):
    try:
        main_pool = [2544, 115, 203999, 203507, 1629029, 1628983, 1630162, 1628369, 1626164, 1641705, 1630595, 1630169]
        additional_younger_players = [1630224, 1630578, 1631094, 1630581, 1630214, 1629628, 1628402, 1629675, 1631096, 1630559]
        eval_pool = list(set(main_pool + additional_younger_players))
        all_players = players.get_players()
        player_dict = {p['id']: p for p in all_players}

        def fetch_data(pid):
            try:
                info = commonplayerinfo.CommonPlayerInfo(player_id=pid, timeout=10).get_normalized_dict()
                p_info = info.get('CommonPlayerInfo', [{}])[0]
                headline = info.get('PlayerHeadlineStats', [{}])[0]
                try:
                    dr_res = get_draftroom_score(player_id=pid, season=season)
                except Exception:
                    dr_res = None
                try:
                    traj_res = get_player_trajectory(player_id=pid, season=season)
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
                    "delta": projected_score - score
                }
            except Exception as e:
                print(f"Error fetching {pid}: {e}")
                return None

        results = []
        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = {executor.submit(fetch_data, pid): pid for pid in eval_pool}
            for future in as_completed(futures):
                res = future.result()
                if res:
                    results.append(res)

        main_results = [r for r in results if r["id"] in main_pool]
        top_prospects = sorted(main_results, key=lambda x: x["score"], reverse=True)[:6]

        breakout_candidates = []
        for r in results:
            pid = r["id"]
            if pid in ALL_STAR_IDS:
                continue
            pts = r["stats"]["pts"]
            delta = r["delta"]
            static_info = player_dict.get(pid, {})
            career_games = static_info.get("career_games")
            if career_games is not None and career_games >= 400:
                continue
            if pts >= 20:
                continue
            if delta <= 0:
                continue
            breakout_candidates.append(r)

        breakout_alerts = sorted(breakout_candidates, key=lambda x: x["delta"], reverse=True)[:3]
        combined = {r["id"]: r for r in top_prospects + breakout_alerts}.values()

        return {
            "data": list(combined),
            "top_prospects": top_prospects,
            "breakout_alerts": breakout_alerts
        }
    except Exception as e:
        print(f"Error in get_batch_scores: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching batch scores: {str(e)}")


@app.get("/players/{player_id}/gamelog")
def get_player_gamelog(player_id: int, season: str = Query("2025-26")):
    try:
        gamelog = playergamelog.PlayerGameLog(player_id=player_id, season=season, timeout=10)
        return gamelog.get_normalized_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching game logs: {str(e)}")


@app.get("/players/{player_id}/draftroom-score")
def get_draftroom_score(player_id: int, season: str = Query("2025-26")):
    try:
        gamelog = playergamelog.PlayerGameLog(player_id=player_id, season=season, timeout=10)
        data = gamelog.get_normalized_dict()
        games = data.get("PlayerGameLog", [])

        if len(games) < 5:
            raise HTTPException(status_code=422, detail="Not enough games to compute score (minimum 5).")

        try:
            info = commonplayerinfo.CommonPlayerInfo(player_id=player_id, timeout=10).get_normalized_dict()
            position = info.get('CommonPlayerInfo', [{}])[0].get('POSITION', '')
        except Exception:
            position = ''

        recent_games = games[:10]
        games_sampled = len(recent_games)

        pts  = sum(g.get('PTS')  or 0 for g in recent_games) / games_sampled
        fga  = sum(g.get('FGA')  or 0 for g in recent_games) / games_sampled
        fta  = sum(g.get('FTA')  or 0 for g in recent_games) / games_sampled
        ast  = sum(g.get('AST')  or 0 for g in recent_games) / games_sampled
        tov  = sum(g.get('TOV')  or 0 for g in recent_games) / games_sampled
        stl  = sum(g.get('STL')  or 0 for g in recent_games) / games_sampled
        blk  = sum(g.get('BLK')  or 0 for g in recent_games) / games_sampled
        dreb = sum(g.get('DREB') or 0 for g in recent_games) / games_sampled

        result = compute_dr_score(pts, fga, fta, ast, tov, stl, blk, dreb, position)

        return {
            "player_id": player_id,
            "draftroom_score": result["draftroom_score"],
            "components": result["components"],
            "games_sampled": games_sampled,
            "season": season
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error computing DraftRoom score: {str(e)}")


@app.get("/players/{player_id}/dr-history")
def get_dr_history(player_id: int, games: str = Query("20"), season: str = Query("2025-26")):
    try:
        try:
            info = commonplayerinfo.CommonPlayerInfo(player_id=player_id, timeout=10).get_normalized_dict()
            position = info.get('CommonPlayerInfo', [{}])[0].get('POSITION', '')
        except Exception:
            position = ''

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
            pts  = g.get('PTS')  or 0
            fga  = g.get('FGA')  or 0
            fta  = g.get('FTA')  or 0
            ast  = g.get('AST')  or 0
            tov  = g.get('TOV')  or 0
            stl  = g.get('STL')  or 0
            blk  = g.get('BLK')  or 0
            dreb = g.get('DREB') or 0

            result = compute_dr_score(pts, fga, fta, ast, tov, stl, blk, dreb, position)
            matchup = g.get("MATCHUP", "")
            opponent = matchup[-3:] if matchup else ""

            history.append({
                "game_number": i + 1,
                "date": g.get("GAME_DATE", ""),
                "opponent": opponent,
                "dr_score": result["draftroom_score"],
                "pts": float(pts),
                "ast": float(ast),
                "reb": float(g.get("REB") or 0)
            })

        return history
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error computing DR history: {str(e)}")


@app.get("/players/{player_id}/trajectory")
def get_player_trajectory(player_id: int, season: str = Query("2025-26")):
    try:
        try:
            info = commonplayerinfo.CommonPlayerInfo(player_id=player_id, timeout=10).get_normalized_dict()
            position = info.get('CommonPlayerInfo', [{}])[0].get('POSITION', '')
        except Exception:
            position = ''

        gamelog = playergamelog.PlayerGameLog(player_id=player_id, season=season, timeout=10)
        data = gamelog.get_normalized_dict()
        games = data.get("PlayerGameLog", [])

        if len(games) < 5:
            raise HTTPException(status_code=422, detail="Not enough games for trajectory.")

        recent_games = games[:10]
        recent_games.reverse()
        n = len(recent_games)

        def parse_min(m):
            if isinstance(m, str) and ':' in m:
                pts = m.split(':')
                return float(pts[0]) + float(pts[1]) / 60.0
            elif m is not None:
                return float(m)
            return 1.0

        mins = [max(parse_min(g.get('MIN', 1)), 1.0) for g in recent_games]
        avg_min = sum(mins) / n

        def get_stat_array(stat_name, is_pts=False):
            arr = []
            for g in recent_games:
                val = g.get(stat_name) or 0
                if is_pts:
                    matchup = g.get('MATCHUP', '')
                    opp = matchup[-3:] if matchup else ''
                    opp_def = DEF_RTG.get(opp, 113.0)
                    adj = opp_def / 113.0
                    val = val / adj if adj > 0 else val
                arr.append(val)
            return arr

        def compute_projection(stat_arr):
            x_pms = [stat_arr[i] / mins[i] for i in range(n)]
            num = sum(0.85 ** (n - 1 - i) * x_pms[i] for i in range(n))
            den = sum(0.85 ** (n - 1 - i) for i in range(n))
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
                var = sum((x - mean_pm) ** 2 for x in x_pms) / n
                cv = math.sqrt(var) / mean_pm
            else:
                cv = 0
            confidence = max(20.0, min(95.0, 100.0 - 120.0 * cv))

            return projected, trend, confidence, x_pms

        pts_proj,  pts_trend,  pts_conf,  pts_pms  = compute_projection(get_stat_array('PTS', True))
        ast_proj,  ast_trend,  ast_conf,  ast_pms  = compute_projection(get_stat_array('AST'))
        reb_proj,  reb_trend,  reb_conf,  reb_pms  = compute_projection(get_stat_array('REB'))
        fga_proj,  _,          _,         fga_pms  = compute_projection(get_stat_array('FGA'))
        fta_proj,  _,          _,         fta_pms  = compute_projection(get_stat_array('FTA'))
        tov_proj,  _,          _,         tov_pms  = compute_projection(get_stat_array('TOV'))
        stl_proj,  _,          _,         stl_pms  = compute_projection(get_stat_array('STL'))
        blk_proj,  _,          _,         blk_pms  = compute_projection(get_stat_array('BLK'))
        dreb_proj, _,          _,         dreb_pms = compute_projection(get_stat_array('DREB'))

        dr_result = compute_dr_score(
            pts_proj, fga_proj, fta_proj, ast_proj, tov_proj,
            stl_proj, blk_proj, dreb_proj, position
        )
        dr_proj = dr_result["draftroom_score"]

        dr_pms = []
        for i in range(n):
            g_pts  = pts_pms[i]  * mins[i]
            g_fga  = fga_pms[i]  * mins[i]
            g_fta  = fta_pms[i]  * mins[i]
            g_ast  = ast_pms[i]  * mins[i]
            g_tov  = tov_pms[i]  * mins[i]
            g_stl  = stl_pms[i]  * mins[i]
            g_blk  = blk_pms[i]  * mins[i]
            g_dreb = dreb_pms[i] * mins[i]
            g_result = compute_dr_score(g_pts, g_fga, g_fta, g_ast, g_tov, g_stl, g_blk, g_dreb, position)
            dr_pms.append(g_result["draftroom_score"] / mins[i])

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
            var = sum((x - mean_pm) ** 2 for x in dr_pms) / n
            cv = math.sqrt(var) / mean_pm
        else:
            cv = 0
        dr_conf = max(20.0, min(95.0, 100.0 - 120.0 * cv))

        return {
            "PTS": {"value": round(pts_proj, 1), "trend": pts_trend, "confidence": round(pts_conf, 1)},
            "AST": {"value": round(ast_proj, 1), "trend": ast_trend, "confidence": round(ast_conf, 1)},
            "REB": {"value": round(reb_proj, 1), "trend": reb_trend, "confidence": round(reb_conf, 1)},
            "DraftRoomScore": {"value": round(dr_proj, 1), "trend": dr_trend, "confidence": round(dr_conf, 1)}
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error computing trajectory: {str(e)}")


@app.get("/players/{player_id}")
def get_player_info(player_id: int):
    try:
        info = commonplayerinfo.CommonPlayerInfo(player_id=player_id, timeout=10)
        return info.get_normalized_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching player info: {str(e)}")


# ─── Debug ────────────────────────────────────────────────────────────────────

@app.get("/debug/injuries")
def debug_injuries():
    fetch_injury_report()
    return {
        "cache_size": len(_injury_cache),
        "sample": dict(list(_injury_cache.items())[:10]),
        "cache_age_seconds": round(time.time() - _injury_cache_time, 1)
    }


# ─── Lineup Optimizer ─────────────────────────────────────────────────────────

class LineupOptimizeRequest(BaseModel):
    player_names: List[str]
    season: str = "2025-26"


@app.post("/lineup/optimize")
def optimize_lineup(request: LineupOptimizeRequest):
    try:
        all_players = players.get_players()
        stripped_lookup = [(strip_accents(p['full_name'].lower()), p) for p in all_players]

        def resolve_name(name: str):
            name_lower = name.strip().lower()
            name_stripped = strip_accents(name_lower)

            if name_lower in NICKNAME_MAP:
                name_lower = NICKNAME_MAP[name_lower]
                name_stripped = strip_accents(name_lower)

            for suffix in [" jr.", " jr", " iii", " ii", " iv", " sr.", " sr"]:
                name_stripped = name_stripped.replace(suffix, "").strip()

            for stripped, p in stripped_lookup:
                if stripped == name_stripped:
                    return p

            parts = name_stripped.split()
            for stripped, p in stripped_lookup:
                if all(part in stripped for part in parts):
                    return p

            if len(parts) >= 2:
                reversed_parts = list(reversed(parts))
                for stripped, p in stripped_lookup:
                    if all(part in stripped for part in reversed_parts):
                        return p

            if len(parts) == 1:
                active_last = [p for s, p in stripped_lookup if p.get('is_active') and parts[0] == s.split()[-1]]
                if active_last: return active_last[0]
                active_any = [p for s, p in stripped_lookup if p.get('is_active') and parts[0] in s]
                if active_any: return active_any[0]
                any_match = [p for s, p in stripped_lookup if parts[0] in s]
                if any_match: return any_match[0]

            for stripped, p in stripped_lookup:
                if any(part in stripped for part in parts if len(part) > 4):
                    return p

            return None

        resolved = []
        unresolved = []
        seen_ids = set()
        for name in request.player_names:
            match = resolve_name(name)
            if match and match['id'] not in seen_ids:
                resolved.append(match)
                seen_ids.add(match['id'])
            elif not match:
                unresolved.append(name)

        if not resolved:
            raise HTTPException(status_code=422, detail="Could not resolve any player names.")

        fetch_injury_report()

        def fetch_player_data(p):
            pid = p['id']
            try:
                dr_result = get_draftroom_score(player_id=pid, season=request.season)
                dr_score = dr_result["draftroom_score"]

                gamelog = playergamelog.PlayerGameLog(player_id=pid, season=request.season, timeout=10)
                data = gamelog.get_normalized_dict()
                games = data.get("PlayerGameLog", [])

                if len(games) < 5:
                    return {"id": pid, "name": p['full_name'], "error": "Not enough games this season", "resolved": True}

                recent = games[:10]

                def parse_min(m):
                    if isinstance(m, str) and ':' in m:
                        pts = m.split(':')
                        return float(pts[0]) + float(pts[1]) / 60.0
                    elif m is not None:
                        return float(m)
                    return 0.0

                mins = [parse_min(g.get('MIN', 0)) for g in recent]
                avg_min = sum(mins) / len(mins) if mins else 0
                last3_min = sum(mins[:3]) / 3 if len(mins) >= 3 else avg_min
                prev_min = sum(mins[3:]) / len(mins[3:]) if len(mins) > 3 else avg_min
                min_delta = last3_min - prev_min

                if abs(min_delta) < 1.5:
                    min_trend = "stable"
                elif min_delta > 0:
                    min_trend = "up"
                else:
                    min_trend = "down"

                try:
                    info = commonplayerinfo.CommonPlayerInfo(player_id=pid, timeout=10).get_normalized_dict()
                    p_info = info.get('CommonPlayerInfo', [{}])[0]
                    position = p_info.get('POSITION', '')
                    team_abbr = p_info.get('TEAM_ABBREVIATION', '')
                except Exception:
                    position = ''
                    team_abbr = ''

                game_scores = []
                for g in recent:
                    g_pts  = g.get('PTS')  or 0
                    g_fga  = g.get('FGA')  or 0
                    g_fta  = g.get('FTA')  or 0
                    g_ast  = g.get('AST')  or 0
                    g_tov  = g.get('TOV')  or 0
                    g_stl  = g.get('STL')  or 0
                    g_blk  = g.get('BLK')  or 0
                    g_dreb = g.get('DREB') or 0
                    g_result = compute_dr_score(g_pts, g_fga, g_fta, g_ast, g_tov, g_stl, g_blk, g_dreb, position)
                    game_scores.append(g_result["draftroom_score"])

                last3_dr = sum(game_scores[:3]) / 3
                prev7_dr = sum(game_scores[3:]) / len(game_scores[3:]) if len(game_scores) > 3 else last3_dr
                dr_delta = last3_dr - prev7_dr

                if abs(dr_delta) < 2.0:
                    dr_trend = "stable"
                elif dr_delta > 0:
                    dr_trend = "up"
                else:
                    dr_trend = "down"

                pts  = sum(g.get('PTS')  or 0 for g in recent) / len(recent)
                ast  = sum(g.get('AST')  or 0 for g in recent) / len(recent)
                stl  = sum(g.get('STL')  or 0 for g in recent) / len(recent)
                blk  = sum(g.get('BLK')  or 0 for g in recent) / len(recent)
                dreb = sum(g.get('DREB') or 0 for g in recent) / len(recent)
                oreb = sum(g.get('OREB') or 0 for g in recent) / len(recent)

                rest_days = None
                try:
                    last_game_date = games[0].get("GAME_DATE", "")
                    if last_game_date:
                        game_dt = datetime.strptime(last_game_date, "%b %d, %Y")
                        rest_days = (datetime.now() - game_dt).days
                except Exception:
                    rest_days = None

                last_matchup = games[0].get("MATCHUP", "")
                opp_abbr = last_matchup[-3:] if last_matchup else ""
                opp_def = DEF_RTG.get(opp_abbr, 113.0)
                sorted_defs = sorted(DEF_RTG.items(), key=lambda x: x[1], reverse=True)
                def_ranks = {team: i + 1 for i, (team, _) in enumerate(sorted_defs)}
                opp_rank = def_ranks.get(opp_abbr, 15)

                if opp_rank <= 5:
                    matchup_label = f"Vs {opp_abbr} (Bottom-5 Defense 🔥)"
                    matchup_boost = True
                elif opp_rank <= 10:
                    matchup_label = f"Vs {opp_abbr} (Weak Defense)"
                    matchup_boost = True
                elif opp_rank >= 25:
                    matchup_label = f"Vs {opp_abbr} (Top-5 Defense ⚠️)"
                    matchup_boost = False
                elif opp_rank >= 20:
                    matchup_label = f"Vs {opp_abbr} (Tough Defense)"
                    matchup_boost = False
                else:
                    matchup_label = f"Vs {opp_abbr} (Neutral Matchup)"
                    matchup_boost = None

                injury_info = get_injury_status(p['full_name'])
                injury_status = injury_info[0] if injury_info else None
                injury_reason = injury_info[1] if injury_info else None
                injury_type   = injury_info[2] if injury_info else ""

                reasons = []

                if injury_status:
                    suspended    = is_suspension(injury_status, injury_type)
                    unavailable  = is_unavailable(injury_status, injury_type)
                    questionable = is_questionable(injury_status)

                    if suspended:
                        short = injury_reason.split(";")[0].replace("Injury/Illness - ", "").strip() if injury_reason else ""
                        reasons.append(f"SUSPENDED{' — ' + short if short else ''} ⚠️")
                    elif unavailable:
                        short = injury_reason.split(";")[0].replace("Injury/Illness - ", "").strip() if injury_reason else ""
                        reasons.append(f"OUT{' — ' + short if short else ''} ⚠️")
                    elif questionable:
                        short = injury_reason.split(";")[0].replace("Injury/Illness - ", "").strip() if injury_reason else ""
                        reasons.append(f"QUESTIONABLE{' — ' + short if short else ''} ⚠️")

                player_unavailable = injury_status and is_unavailable(injury_status, injury_type)
                if not player_unavailable:
                    if dr_score >= 75:
                        reasons.append("Elite DR Score")
                    elif dr_score >= 65:
                        reasons.append("Strong DR Score")

                    if dr_trend == "up" and abs(dr_delta) >= 3:
                        reasons.append(f"+{dr_delta:.1f} DR Over Last 3 Games — Trending Up")
                    elif dr_trend == "down" and abs(dr_delta) >= 3:
                        reasons.append(f"{dr_delta:.1f} DR Over Last 3 Games — Trending Down")

                    if min_trend == "up" and abs(min_delta) >= 2:
                        reasons.append(f"Minutes Up {min_delta:.1f} MPG — Increased Role")
                    elif min_trend == "down" and abs(min_delta) >= 2:
                        reasons.append(f"Minutes Down {abs(min_delta):.1f} MPG — Reduced Role, Monitor")

                    reasons.append(matchup_label)

                    if not injury_status:
                        if rest_days is not None and 2 <= rest_days <= 14:
                            reasons.append(f"{rest_days} Days Rest — Fresh Legs")
                        elif rest_days == 0:
                            reasons.append("Back-To-Back — Fatigue Risk")

                start_score = dr_score

                if injury_status:
                    if is_unavailable(injury_status, injury_type):
                        start_score -= 50
                    elif is_questionable(injury_status):
                        start_score -= 10

                if matchup_boost is True:
                    start_score += 4
                elif matchup_boost is False:
                    start_score -= 3
                if min_trend == "up":
                    start_score += 2
                elif min_trend == "down":
                    start_score -= 3
                if rest_days is not None and not injury_status and 2 <= rest_days <= 14:
                    start_score += 1.5
                elif rest_days == 0:
                    start_score -= 2

                return {
                    "id": pid,
                    "name": p['full_name'],
                    "position": position,
                    "team": team_abbr,
                    "dr_score": dr_score,
                    "dr_trend": dr_trend,
                    "dr_delta": round(dr_delta, 1),
                    "minutes_avg": round(avg_min, 1),
                    "minutes_trend": min_trend,
                    "minutes_delta": round(min_delta, 1),
                    "opp_abbr": opp_abbr,
                    "opp_def_rating": opp_def,
                    "opp_rank": opp_rank,
                    "matchup_label": matchup_label,
                    "matchup_boost": matchup_boost,
                    "rest_days": rest_days,
                    "injury_status": injury_status,
                    "injury_reason": injury_reason,
                    "start_score": round(start_score, 1),
                    "tier": "",
                    "reasons": reasons,
                    "stats": {
                        "pts": round(pts, 1),
                        "ast": round(ast, 1),
                        "reb": round(dreb + oreb, 1),
                        "stl": round(stl, 1),
                        "blk": round(blk, 1)
                    },
                    "recommended_start": False,
                    "error": None
                }

            except Exception as e:
                print(f"Error fetching optimize data for {pid}: {e}")
                return {"id": pid, "name": p['full_name'], "error": str(e), "resolved": True}

        results = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(fetch_player_data, p): p for p in resolved}
            for future in as_completed(futures):
                res = future.result()
                if res:
                    results.append(res)

        valid = [r for r in results if not r.get("error")]
        errored = [r for r in results if r.get("error")]
        valid.sort(key=lambda x: x.get("start_score", 0), reverse=True)

        for i, r in enumerate(valid):
            injury_status = r.get("injury_status", "") or ""
            injury_type   = r.get("injury_reason", "") or ""

            if is_unavailable(injury_status, injury_type):
                r["recommended_start"] = False
                r["tier"] = "Suspended" if is_suspension(injury_status, injury_type) else "Injured"
                continue

            score = r["start_score"]
            if i < 5:
                r["recommended_start"] = True
                if score >= 75:
                    r["tier"] = "Lock In"
                elif score >= 65:
                    r["tier"] = "Start"
                elif score >= 55:
                    r["tier"] = "Monitor"
                else:
                    r["tier"] = "Sit"
            else:
                r["recommended_start"] = False
                if score >= 65:
                    r["tier"] = "Top Reserve"
                elif score >= 55:
                    r["tier"] = "Solid Bench"
                else:
                    r["tier"] = "Deep Cut"

        starters = [r for r in valid if r["recommended_start"]]
        bench    = [r for r in valid if not r["recommended_start"]]
        starters.sort(key=lambda x: POSITION_ORDER.get(x.get("position", ""), 99))

        return {
            "players": starters + bench,
            "unresolved_names": unresolved,
            "errored_players": errored,
            "total_resolved": len(resolved),
            "roster_size": len(request.player_names)
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in optimize_lineup: {e}")
        raise HTTPException(status_code=500, detail=f"Error optimizing lineup: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
