const PLAYERS_HASH = {
  "p_1a2b3c": "bala",
  "p_4d5e6f": "bob",
  "p_7g8h9i": "duck",
  "p_0j1k2l": "Neil",
  "p_3m4n5o": "DHYellow",
  "p_6p7q8r": "PG",
  "p_leaderboard": "ALL_STATS"
};

const PLAYERS = ["bala", "bob", "duck", "Neil", "DHYellow", "PG"];
const RANK_THRESHOLD = 10;
const STATS_SHEET_NAME = "GameStats";

// Secret key for the FULL view (fun stats + season scores). Without `&k=<key>` the API returns
// only the fun statistics, so the public link cannot reveal season scores.
const FULL_VIEW_KEY = "WuXPCletRB7V";

const GOV_NAMES_ZH = {
  "DESPOTISM": "獨裁",
  "MONARCHY": "君主",
  "THEOCRACY": "神權政治",
  "REPUBLIC": "共和",
  "CONSTITUTIONAL_MONARCHY": "君主立憲",
  "COMMUNISM": "共產主義",
  "FUNDAMENTALISM": "基本教義",
  "DEMOCRACY": "民主"
};

function getOrCreateStatsSheet(ss) {
  let sheet = ss.getSheetByName(STATS_SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(STATS_SHEET_NAME);
    sheet.appendRow(["Timestamp", "GameID", "R1", "R2", "R3", "R4", "StatsJSON"]);
  }
  return sheet;
}

function doGet(e) {
  const response = ContentService.createTextOutput();
  response.setMimeType(ContentService.MimeType.JSON);

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheets()[0];
  const rows = sheet.getDataRange().getValues();
  const statsSheet = getOrCreateStatsSheet(ss);
  const statsRows = statsSheet.getDataRange().getValues();

  // Map GameID -> parsed gameStats JSON
  let parsedGamesMap = {};
  for (let i = 1; i < statsRows.length; i++) {
    let gid = String(statsRows[i][1] || "").trim().toUpperCase();
    let rawJson = statsRows[i][6];
    if (gid && rawJson) {
      try {
        parsedGamesMap[gid] = JSON.parse(rawJson);
      } catch (err) {}
    }
  }

  // Action: return current 1st-4th rankings of every GameID (used by auto_collector.py to
  // detect mismatches before overwriting manually entered results)
  const fullView = !!(e && e.parameter && e.parameter.k === FULL_VIEW_KEY);

  if (e && e.parameter && e.parameter.action === "ranks") {
    if (!fullView) return response.setContent(JSON.stringify({ error: "需要完整版金鑰" }));
    let ranks = {};
    for (let i = 1; i < rows.length; i++) {
      let gid = String(rows[i][1] || "").trim().toUpperCase();
      if (gid) ranks[gid] = [rows[i][2], rows[i][3], rows[i][4], rows[i][5]].map(x => String(x || "").trim());
    }
    return response.setContent(JSON.stringify({ ranks: ranks }));
  }

  // Action: return list of unparsed GameIDs for auto_collector.py
  if (e && e.parameter && e.parameter.action === "unparsed") {
    let unparsed = [];
    for (let i = 1; i < rows.length; i++) {
      let gid = String(rows[i][1] || "").trim().toUpperCase();
      if (gid && (!parsedGamesMap[gid] || !rows[i][2])) {
        unparsed.push(gid);
      }
    }
    return response.setContent(JSON.stringify({
      unparsedGameIds: unparsed,
      totalParsed: Object.keys(parsedGamesMap).length
    }));
  }

  const hash = e && e.parameter ? e.parameter.p : null;
  if (!hash || !PLAYERS_HASH[hash]) {
    return response.setContent(JSON.stringify({ error: "無效的專屬連結" }));
  }

  const targetPlayer = PLAYERS_HASH[hash];

  let allStats = {};
  let recentStats = {};
  let matrix = {};
  let timeline = [];
  let runningStats = {};
  let history = {};
  let activityObj = {};
  let playerMonths = {};
  let totalGroupGames = 0;

  PLAYERS.forEach(p => {
    allStats[p] = { name: p, totalGames: 0, firstPlace: 0, top2Place: 0, totalScore: 0, ranks: [0, 0, 0, 0, 0] };
    recentStats[p] = { name: p, totalGames: 0, firstPlace: 0, top2Place: 0, totalScore: 0 };
    runningStats[p] = { games: 0, wins: 0 };
    history[p] = [];
    playerMonths[p] = new Set();
    matrix[p] = {};
    PLAYERS.forEach(opp => {
      if (p !== opp) matrix[p][opp] = { games: 0, beatThem: 0 };
    });
  });

  const now = new Date();
  const thirtyDaysAgo = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);

  for (let i = 1; i < rows.length; i++) {
    let r = rows[i];
    let gameDate = new Date(r[0]);
    let gamePlayers = [r[2], r[3], r[4], r[5]];

    if (!gamePlayers[0]) continue;

    totalGroupGames++;
    let isRecent = gameDate >= thirtyDaysAgo;
    let monthStr = gameDate.getFullYear() + "-" + String(gameDate.getMonth() + 1).padStart(2, '0');

    if (!activityObj[monthStr]) {
      activityObj[monthStr] = { month: monthStr, total: 0 };
      PLAYERS.forEach(p => activityObj[monthStr][p] = 0);
    }
    activityObj[monthStr].total++;

    gamePlayers.forEach((p, idx) => {
      if (allStats[p]) {
        let rank = idx + 1;
        let score = [0, 8, 5, 3, 2][rank];

        allStats[p].totalGames++;
        allStats[p].ranks[rank]++;
        if (rank === 1) allStats[p].firstPlace++;
        if (rank <= 2) allStats[p].top2Place++;
        allStats[p].totalScore += score;

        history[p].push(score);
        activityObj[monthStr][p]++;
        playerMonths[p].add(monthStr);

        if (isRecent) {
          recentStats[p].totalGames++;
          if (rank === 1) recentStats[p].firstPlace++;
          if (rank <= 2) recentStats[p].top2Place++;
          recentStats[p].totalScore += score;
        }

        for (let j = 0; j < 4; j++) {
          let opp = gamePlayers[j];
          if (opp && opp !== p && matrix[p][opp]) {
            matrix[p][opp].games++;
            if (rank < (j + 1)) matrix[p][opp].beatThem++;
          }
        }

        runningStats[p].games++;
        if (rank === 1) runningStats[p].wins++;
      }
    });

    let snapshot = { date: gameDate.toISOString(), players: {} };
    PLAYERS.forEach(p => {
      snapshot.players[p] = runningStats[p].games > 0 ? parseFloat(((runningStats[p].wins / runningStats[p].games) * 100).toFixed(1)) : null;
    });
    timeline.push(snapshot);
  }

  let activityArr = Object.values(activityObj).sort((a, b) => a.month.localeCompare(b.month));
  let allMonthsSorted = activityArr.map(a => a.month);

  const calcDerived = (s) => {
    s.avgScore = s.totalGames > 0 ? parseFloat((s.totalScore / s.totalGames).toFixed(2)) : 0;
    s.firstPct = s.totalGames > 0 ? parseFloat(((s.firstPlace / s.totalGames) * 100).toFixed(1)) : 0;
    s.top2Pct = s.totalGames > 0 ? parseFloat(((s.top2Place / s.totalGames) * 100).toFixed(1)) : 0;
  };

  PLAYERS.forEach(p => {
    calcDerived(allStats[p]);
    calcDerived(recentStats[p]);
    PLAYERS.forEach(opp => {
      if (p !== opp) {
        let m = matrix[p][opp];
        m.winPct = m.games > 0 ? parseFloat(((m.beatThem / m.games) * 100).toFixed(1)) : null;
      }
    });
  });

  const getRank = (statsObj, metric) => {
    let eligible = PLAYERS.filter(p => statsObj[p].totalGames > RANK_THRESHOLD).map(p => statsObj[p]);
    eligible.sort((a, b) => b[metric] - a[metric]);
    let ranks = {};
    for (let i = 0; i < eligible.length; i++) {
      if (i > 0 && eligible[i][metric] === eligible[i - 1][metric]) {
        ranks[eligible[i].name] = ranks[eligible[i - 1].name];
      } else {
        ranks[eligible[i].name] = i + 1;
      }
    }
    return ranks;
  };

  const getStreak = (p) => {
    if (allMonthsSorted.length === 0) return 0;
    let streak = 0;
    for (let i = allMonthsSorted.length - 1; i >= 0; i--) {
      if (playerMonths[p].has(allMonthsSorted[i])) streak++;
      else break;
    }
    return streak;
  };

  const getBestTablemate = (p) => {
    let bestOpp = "無";
    let maxGames = 0;
    PLAYERS.forEach(opp => {
      if (p !== opp && matrix[p][opp].games > maxGames) {
        maxGames = matrix[p][opp].games;
        bestOpp = opp;
      }
    });
    return { name: bestOpp, games: maxGames };
  };

  // =========================================================================
  // Aggregate Replay Fun Statistics Across All Parsed Games
  // =========================================================================
  const funBundle = computeFunStatistics(parsedGamesMap, allStats);

  if (targetPlayer === "ALL_STATS") {
    if (!fullView) {
      return response.setContent(JSON.stringify({
        isLeaderboard: true,
        fullView: false,
        totalGroupGames: totalGroupGames,
        funLeaderboard: funBundle.leaderboard
      }));
    }
    let allData = PLAYERS.map(p => allStats[p]);
    allData.sort((a, b) => b.avgScore - a.avgScore);
    return response.setContent(JSON.stringify({
      isLeaderboard: true,
      fullView: true,
      data: allData,
      matrix: matrix,
      timeline: timeline,
      activity: activityArr,
      totalGroupGames: totalGroupGames,
      funLeaderboard: funBundle.leaderboard
    }));
  }

  if (!fullView) {
    return response.setContent(JSON.stringify({
      isLeaderboard: false,
      fullView: false,
      name: targetPlayer,
      allTime: { totalGames: allStats[targetPlayer].totalGames },
      streak: getStreak(targetPlayer),
      bestMate: getBestTablemate(targetPlayer),
      totalGroupGames: totalGroupGames,
      funPersonal: funBundle.personal[targetPlayer]
    }));
  }

  let tAll = allStats[targetPlayer];
  tAll.rankAvg = getRank(allStats, 'avgScore')[targetPlayer] || "-";
  tAll.rankFirst = getRank(allStats, 'firstPct')[targetPlayer] || "-";
  tAll.rankTop2 = getRank(allStats, 'top2Pct')[targetPlayer] || "-";

  let tRec = recentStats[targetPlayer];
  tRec.rankAvg = getRank(recentStats, 'avgScore')[targetPlayer] || "-";
  tRec.rankFirst = getRank(recentStats, 'firstPct')[targetPlayer] || "-";
  tRec.rankTop2 = getRank(recentStats, 'top2Pct')[targetPlayer] || "-";

  return response.setContent(JSON.stringify({
    isLeaderboard: false,
    fullView: true,
    name: targetPlayer,
    allTime: tAll,
    recent: tRec,
    timeline: timeline,
    history: history[targetPlayer],
    activity: activityArr,
    streak: getStreak(targetPlayer),
    bestMate: getBestTablemate(targetPlayer),
    totalGroupGames: totalGroupGames,
    funPersonal: funBundle.personal[targetPlayer],
    seatStats: funBundle.personalSeats[targetPlayer]
  }));
}

// ============================================================================
// v3 Fun statistics + data analysis (replaces previous computeFunStatistics)
// ============================================================================
const MIN_M5_GAMES = 5;      // 被逆轉王 / 逆轉勝王: at least 5 relevant games
const MIN_AGGR_SAMPLES = 5;  // 侵略成功率 / 防禦成功率: at least 5 aggressions
const MIN_WAR_SAMPLES = 3;   // 反打王 / 普丁: at least 3 resolved wars
const SEASON_POINTS = [0, 8, 5, 3, 2];

// Award categories (display order)
const AWARD_CATEGORIES = [
  { id: "result", title: "🏆 勝負與逆轉" },
  { id: "war", title: "⚔️ 戰爭" },
  { id: "aggr", title: "🗡️ 侵略與防守" },
  { id: "politics", title: "🕊️ 外交、政治與事件" },
  { id: "build", title: "🏛️ 建設與奇觀" },
  { id: "production", title: "📈 產能與行動紀錄" },
  { id: "stock", title: "📦 庫存紀錄" },
  { id: "style", title: "🎲 風格與災難" }
];

// [stat key, JSON field written by tta_parser.py] -> single-game records (with GameID + round)
const REC_FIELDS = [
  ["maxFoodRate", "max_food_rate"],
  ["maxResRate", "max_resource_rate"],
  ["maxSciRate", "max_science_rate"],
  ["maxCultRate", "max_culture_rate"],
  ["maxStrength", "max_strength"],
  ["maxCA", "max_ca"],
  ["maxMA", "max_ma"],
  ["maxUrbanInGame", "max_urban_workers"],
  ["maxFoodStock", "max_food_stock"],
  ["maxResStock", "max_resource_stock"],
  ["maxSciStock", "max_science_stock"],
  ["maxWarCulture", "max_war_culture_gained"],
  ["maxWarCultureLost", "max_war_culture_lost"],
  ["maxImpactCulture", "max_impact_culture_gained"],
  ["maxSingleWonderScore", "max_single_wonder_score"],
  ["ratsFoodLostMax", "rats_food_lost"],
  ["darkAgesSciLostMax", "dark_ages_science_lost"],
  ["maxAggrWinsInGame", "aggressions_succeeded"],
  ["maxAggrDefendedInGame", "aggressions_defended"],
  ["maxWarDemolitions", "war_demolitions_max"]
];

function pearson(xs, ys) {
  let n = xs.length;
  if (n < 3) return null;
  let mx = xs.reduce((a, b) => a + b, 0) / n;
  let my = ys.reduce((a, b) => a + b, 0) / n;
  let sxy = 0, sxx = 0, syy = 0;
  for (let i = 0; i < n; i++) {
    sxy += (xs[i] - mx) * (ys[i] - my);
    sxx += (xs[i] - mx) * (xs[i] - mx);
    syy += (ys[i] - my) * (ys[i] - my);
  }
  if (sxx === 0 || syy === 0) return null;
  return parseFloat((sxy / Math.sqrt(sxx * syy)).toFixed(3));
}

function computeFunStatistics(parsedGamesMap, allStats) {
  let pf = {};
  let globalLeadersByAge = { "A": {}, "I": {}, "II": {}, "III": {} };
  let globalWondersByAge = { "A": {}, "I": {}, "II": {}, "III": {} };
  // Seat (turn order) analysis: seat 1..4
  let seats = [1, 2, 3, 4].map(n => ({ seat: n, games: 0, points: 0, first: 0, top2: 0 }));
  let personalSeats = {};
  // Production vs rank correlation samples
  let corr = {};
  ["food", "res", "sci", "pop", "ca", "ma"].forEach(k => { corr[k] = { xs: [], ys: [], byRank: [[], [], [], [], []] }; });

  PLAYERS.forEach(p => {
    let st = {
      name: p,
      parsedGames: 0,
      totalGames: allStats[p] ? allStats[p].totalGames : 0,
      leadersTotal: 0, femaleLeaders: 0, femaleLeaderCounts: {},
      leadersByAge: { "A": {}, "I": {}, "II": {}, "III": {} },
      wondersByAge: { "A": {}, "I": {}, "II": {}, "III": {} },
      govCounts: {},
      warsDeclared: 0, warsOnMe: 0,
      warsWonAtt: 0, warsLostAtt: 0, warsWonDef: 0, warsLostDef: 0, warDemolitions: 0,
      aggrDeclared: 0, aggrSucceeded: 0, aggrOnMe: 0, aggrSucceededOnMe: 0, aggrDefended: 0,
      leadingM5Games: 0, chokedGames: 0, trailingM5Games: 0, comebackWins: 0,
      wondersTotal: 0, pactsTotal: 0, pactsBrokenTotal: 0, coloniesTotal: 0,
      revolutionsTotal: 0, peacefulGovTotal: 0, uprisingsTotal: 0, peaceGames: 0,
      ratsFoodLostTotal: 0, darkAgesSciLostTotal: 0,
      eventsPlayed: 0, threeCATakes: 0, resigns: 0,
      tieGames: 0, winByOneGames: 0, loseByOneGames: 0,
      maxImpactName: "", maxSingleWonderName: "",
      maxWondersInGame: 0,
      at: {}   // record key -> { g: GameID, r: round }
    };
    REC_FIELDS.forEach(([k]) => { st[k] = 0; st.at[k] = null; });
    st.at.maxWondersInGame = null;
    pf[p] = st;
    personalSeats[p] = [1, 2, 3, 4].map(n => ({ seat: n, games: 0, points: 0, first: 0, top2: 0 }));
  });

  Object.keys(parsedGamesMap).forEach(gid => {
    let g = parsedGamesMap[gid];
    if (!g) return;
    let pMap = g.players || g;
    let finals = [];   // { name, culture } of players who did not resign
    let nPlayers = Object.keys(pMap).length;

    Object.keys(pMap).forEach(pKey => {
      let ps = pMap[pKey];
      let pName = ps.player || pKey;
      let rank = ps.rank || 0;

      // Global analyses (all players, incl. non-tournament)
      if (nPlayers === 4 && rank >= 1 && rank <= 4 && ps.slot !== undefined && ps.slot >= 0 && ps.slot <= 3) {
        let sObj = seats[ps.slot];
        sObj.games++; sObj.points += SEASON_POINTS[rank];
        if (rank === 1) sObj.first++;
        if (rank <= 2) sObj.top2++;
        if (personalSeats[pName]) {
          let ps2 = personalSeats[pName][ps.slot];
          ps2.games++; ps2.points += SEASON_POINTS[rank];
          if (rank === 1) ps2.first++;
          if (rank <= 2) ps2.top2++;
        }
        if (ps.total_food_net !== undefined && !ps.resigned) {
          [["food", ps.total_food_net], ["res", ps.total_resource], ["sci", ps.total_science],
           ["pop", ps.final_population], ["ca", ps.total_ca], ["ma", ps.total_ma]].forEach(([k, v]) => {
            if (v === undefined) return;
            corr[k].xs.push(v); corr[k].ys.push(rank); corr[k].byRank[rank].push(v);
          });
        }
      }
      if (!ps.resigned && ps.final_culture !== undefined) finals.push({ name: pName, culture: ps.final_culture });

      if (!pf[pName]) return;
      let st = pf[pName];
      st.parsedGames++;

      (ps.leaders_played || []).forEach(item => {
        let age = item.age || "A";
        let zh = item.name_zh || item.card || "";
        if (!zh) return;
        st.leadersTotal++;
        if (item.is_female) {
          st.femaleLeaders++;
          st.femaleLeaderCounts[zh] = (st.femaleLeaderCounts[zh] || 0) + 1;
        }
        if (st.leadersByAge[age]) st.leadersByAge[age][zh] = (st.leadersByAge[age][zh] || 0) + 1;
        if (globalLeadersByAge[age]) globalLeadersByAge[age][zh] = (globalLeadersByAge[age][zh] || 0) + 1;
      });

      let wList = ps.wonders_completed || [];
      let wCount = ps.wonders_count !== undefined ? ps.wonders_count : wList.length;
      st.wondersTotal += wCount;
      if (wCount > st.maxWondersInGame) {
        st.maxWondersInGame = wCount;
        st.at.maxWondersInGame = { g: gid, r: 0 };
      }
      wList.forEach(item => {
        let age = item.age || "A";
        let zh = item.name_zh || item.card || "";
        if (!zh) return;
        if (st.wondersByAge[age]) st.wondersByAge[age][zh] = (st.wondersByAge[age][zh] || 0) + 1;
        if (globalWondersByAge[age]) globalWondersByAge[age][zh] = (globalWondersByAge[age][zh] || 0) + 1;
      });

      // Governments (Despotism is everyone's starting government -> not a "favourite")
      (ps.governments_used || []).forEach(gCard => {
        if (gCard === "DESPOTISM") return;
        let gZh = GOV_NAMES_ZH[gCard] || gCard;
        st.govCounts[gZh] = (st.govCounts[gZh] || 0) + 1;
      });

      st.warsDeclared += (ps.wars_declared || 0);
      st.warsOnMe += (ps.wars_declared_on_me || 0);
      st.warsWonAtt += (ps.wars_won_attacking || 0);
      st.warsLostAtt += (ps.wars_lost_attacking || 0);
      st.warsWonDef += (ps.wars_won_defending || 0);
      st.warsLostDef += (ps.wars_lost_defending || 0);
      st.warDemolitions += (ps.war_demolitions || 0);
      st.aggrDeclared += (ps.aggressions_declared || 0);
      st.aggrSucceeded += (ps.aggressions_succeeded || 0);
      st.aggrOnMe += (ps.aggressions_on_me || 0);
      st.aggrSucceededOnMe += (ps.aggressions_succeeded_on_me || 0);
      st.aggrDefended += Math.max(0, (ps.aggressions_on_me || 0) - (ps.aggressions_succeeded_on_me || 0));
      if (!(ps.wars_declared || 0) && !(ps.aggressions_declared || 0)) st.peaceGames++;

      if (ps.was_leading_5_turns_before_end) st.leadingM5Games++;
      if (ps.was_choked) st.chokedGames++;
      if (ps.was_trailing_5_turns_before_end) st.trailingM5Games++;
      if (ps.was_comeback_win) st.comebackWins++;

      st.pactsTotal += (ps.pacts_signed || 0);
      st.pactsBrokenTotal += (ps.pacts_broken || 0);
      st.coloniesTotal += (ps.colonies_won || 0);
      st.revolutionsTotal += (ps.revolutions || 0);
      st.peacefulGovTotal += (ps.peaceful_gov_changes || 0);
      st.uprisingsTotal += (ps.uprisings || 0);
      st.ratsFoodLostTotal += (ps.rats_food_lost || 0);
      st.darkAgesSciLostTotal += (ps.dark_ages_science_lost || 0);
      st.eventsPlayed += (ps.events_played || 0);
      st.threeCATakes += (ps.three_ca_takes || 0);
      if (ps.resigned) st.resigns++;

      // Single-game records, remembering GameID + round
      REC_FIELDS.forEach(([k, j]) => {
        let v = ps[j];
        if (v === undefined && j === "max_urban_workers") v = ps.urban_buildings_built;
        if (v === undefined && j === "aggressions_defended") v = Math.max(0, (ps.aggressions_on_me || 0) - (ps.aggressions_succeeded_on_me || 0));
        v = v || 0;
        if (v > st[k]) {
          st[k] = v;
          st.at[k] = { g: gid, r: ps[j + "_round"] || 0 };
          if (k === "maxImpactCulture") st.maxImpactName = ps.max_impact_card_name || "";
          if (k === "maxSingleWonderScore") st.maxSingleWonderName = ps.max_single_wonder_name || "";
        }
      });
    });

    // Ties and one-point margins (resigned players excluded), counted once per game
    finals.forEach(a => {
      if (!pf[a.name]) return;
      let tie = false, win1 = false, lose1 = false;
      finals.forEach(b => {
        if (a === b) return;
        let d = a.culture - b.culture;
        if (d === 0) tie = true;
        if (d === 1) win1 = true;
        if (d === -1) lose1 = true;
      });
      if (tie) pf[a.name].tieGames++;
      if (win1) pf[a.name].winByOneGames++;
      if (lose1) pf[a.name].loseByOneGames++;
    });
  });

  PLAYERS.forEach(p => {
    let st = pf[p];
    let g = Math.max(1, st.parsedGames);
    const per = (x) => st.parsedGames > 0 ? parseFloat((x / g).toFixed(2)) : 0;
    const pct = (a, b) => b > 0 ? parseFloat(((a / b) * 100).toFixed(1)) : 0;
    st.femaleRate = pct(st.femaleLeaders, st.leadersTotal);
    st.warRate = per(st.warsDeclared);
    st.aggrWinRate = per(st.aggrSucceeded);
    st.warVictimRate = per(st.warsOnMe);
    st.aggrVictimRate = per(st.aggrSucceededOnMe);
    st.chokeRate = pct(st.chokedGames, st.leadingM5Games);
    st.comebackRate = pct(st.comebackWins, st.trailingM5Games);
    st.wonderAvg = per(st.wondersTotal);
    st.pactAvg = per(st.pactsTotal);
    st.pactBreakAvg = per(st.pactsBrokenTotal);
    st.colonyAvg = per(st.coloniesTotal);
    st.revAvg = per(st.revolutionsTotal);
    st.uprisingAvg = per(st.uprisingsTotal);
    st.peaceRate = pct(st.peaceGames, st.parsedGames);
    st.defendAvg = per(st.aggrDefended);
    st.leadRate = pct(st.leadingM5Games, st.parsedGames);
    st.peacefulGovAvg = per(st.peacefulGovTotal);
    let favGov = "", favCnt = 0;
    Object.keys(st.govCounts).forEach(k => { if (st.govCounts[k] > favCnt) { favCnt = st.govCounts[k]; favGov = k; } });
    st.govLoyalty = pct(favCnt, st.parsedGames);
    st.govLoyaltyName = favGov;
    st.govLoyaltyCount = favCnt;
    // v3
    st.aggrSuccessRate = pct(st.aggrSucceeded, st.aggrDeclared);
    st.defendSuccessRate = pct(st.aggrDefended, st.aggrOnMe);
    st.eventAvg = per(st.eventsPlayed);
    st.threeCAAvg = per(st.threeCATakes);
    st.tieRate = pct(st.tieGames, st.parsedGames);
    st.winByOneRate = pct(st.winByOneGames, st.parsedGames);
    st.loseByOneRate = pct(st.loseByOneGames, st.parsedGames);
    st.resignRate = pct(st.resigns, st.parsedGames);
    st.warsDefResolved = st.warsWonDef + st.warsLostDef;
    st.warsAttResolved = st.warsWonAtt + st.warsLostAtt;
    st.counterWinRate = pct(st.warsWonDef, st.warsDefResolved);
    st.putinRate = pct(st.warsLostAtt, st.warsAttResolved);
  });

  const at = (st, k) => {
    let a = st.at[k];
    if (!a) return "";
    return a.r ? `｜${a.g} 第${a.r}回合` : `｜${a.g}`;
  };

  const getTopCountItem = (counterObj) => {
    let bestKey = "尚無紀錄";
    let bestCnt = 0;
    Object.keys(counterObj || {}).forEach(k => {
      if (counterObj[k] > bestCnt) { bestCnt = counterObj[k]; bestKey = k; }
    });
    return { name: bestKey, count: bestCnt };
  };

  const eligiblePlayers = PLAYERS.filter(p => (allStats[p] && allStats[p].totalGames > RANK_THRESHOLD));

  const AWARD_DEFS = [
    // 🏆 勝負與逆轉
    { cat: "result", id: "lead_rate", icon: "🐎", title: "起跑領先・前段班常客", subtitle: "領跑王（倒數五回合前文化領先的場次比例最高）", metric: "leadRate", unit: "%", descFn: (st) => `領跑場次 ${st.leadRate}% (${st.leadingM5Games}/${st.parsedGames})` },
    { cat: "result", id: "comeback_rate", icon: "🔥", title: "絕地武士・逆轉勝王", subtitle: `逆轉勝王（倒數五回合前最低卻奪冠比率，需 ≥${MIN_M5_GAMES} 場落後）`, metric: "comebackRate", unit: "%", eligibleFn: (st) => st.trailingM5Games >= MIN_M5_GAMES, descFn: (st) => `逆轉勝率 ${st.comebackRate}% (${st.comebackWins}/${st.trailingM5Games})` },
    { cat: "result", id: "choke_rate", icon: "🍾", title: "半場開香檳・被逆轉王", subtitle: `被逆轉王（倒數五回合前最高卻落敗比率，需 ≥${MIN_M5_GAMES} 場領先）`, metric: "chokeRate", unit: "%", eligibleFn: (st) => st.leadingM5Games >= MIN_M5_GAMES, descFn: (st) => `被逆轉率 ${st.chokeRate}% (${st.chokedGames}/${st.leadingM5Games})` },
    { cat: "result", id: "tie_rate", icon: "🤝", title: "心有靈犀・同分王", subtitle: "同分王（最常和其他玩家同分，不論名次）", metric: "tieRate", unit: "%", descFn: (st) => `同分場次 ${st.tieRate}% (${st.tieGames}/${st.parsedGames})` },
    { cat: "result", id: "win_by_one", icon: "🤏", title: "險勝一分・精算大師", subtitle: "險勝王（最常剛好贏對手 1 分，不論名次）", metric: "winByOneRate", unit: "%", descFn: (st) => `險勝 1 分場次 ${st.winByOneRate}% (${st.winByOneGames}/${st.parsedGames})` },
    { cat: "result", id: "lose_by_one", icon: "😭", title: "差一點點・一分之差王", subtitle: "惜敗王（最常剛好輸對手 1 分，不論名次）", metric: "loseByOneRate", unit: "%", descFn: (st) => `惜敗 1 分場次 ${st.loseByOneRate}% (${st.loseByOneGames}/${st.parsedGames})` },
    { cat: "result", id: "resign_rate", icon: "🏳️", title: "瀟灑揮手・投降王", subtitle: "投降王（投降場次比例最高）", metric: "resignRate", unit: "%", descFn: (st) => `投降 ${st.resigns} 次 (${st.resignRate}%)` },
    // ⚔️ 戰爭
    { cat: "war", id: "war_rate", icon: "⚔️", title: "窮兵黷武・戰爭狂人", subtitle: "好戰王（場均宣戰次數最高）", metric: "warRate", unit: "次/場", descFn: (st) => `場均宣戰 ${st.warRate} 次 (共 ${st.warsDeclared} 次)` },
    { cat: "war", id: "war_victim_rate", icon: "🎯", title: "全球公敵・嘲諷滿點", subtitle: "苦主王（場均被宣戰次數最高）", metric: "warVictimRate", unit: "次/場", descFn: (st) => `場均被宣戰 ${st.warVictimRate} 次 (共 ${st.warsOnMe} 次)` },
    { cat: "war", id: "counter_win", icon: "🥊", title: "以逸待勞・反打王", subtitle: `反打王（被宣戰卻反而戰勝的比率最高，需 ≥${MIN_WAR_SAMPLES} 場已結算戰爭）`, metric: "counterWinRate", unit: "%", eligibleFn: (st) => st.warsDefResolved >= MIN_WAR_SAMPLES, descFn: (st) => `反打成功 ${st.counterWinRate}% (${st.warsWonDef}/${st.warsDefResolved})` },
    { cat: "war", id: "putin", icon: "🐻", title: "出師不利・普丁", subtitle: `普丁（宣戰別人卻反而戰敗的比率最高，需 ≥${MIN_WAR_SAMPLES} 場已結算戰爭）`, metric: "putinRate", unit: "%", eligibleFn: (st) => st.warsAttResolved >= MIN_WAR_SAMPLES, descFn: (st) => `宣戰後戰敗 ${st.putinRate}% (${st.warsLostAtt}/${st.warsAttResolved})` },
    { cat: "war", id: "north_korea", icon: "🚀", title: "勒緊褲帶・北韓", subtitle: "北韓（單次宣戰的當回合，拆掉最多農場、礦場、城市建築）", metric: "maxWarDemolitions", unit: "棟", descFn: (st) => `單次宣戰拆了 ${st.maxWarDemolitions} 棟${at(st, "maxWarDemolitions")}（累計 ${st.warDemolitions} 棟）` },
    { cat: "war", id: "war_culture_max", icon: "🏧", title: "人肉提款・提款王", subtitle: "提款王（單場戰爭奪走最多文化）", metric: "maxWarCulture", unit: "分", descFn: (st) => `單場戰爭奪走 ${st.maxWarCulture} 文化${at(st, "maxWarCulture")}` },
    { cat: "war", id: "war_culture_lost_max", icon: "💸", title: "行動提款機・提款機王", subtitle: "提款機王（單場戰爭被奪走最多文化）", metric: "maxWarCultureLost", unit: "分", descFn: (st) => `單場戰爭被奪走 ${st.maxWarCultureLost} 文化${at(st, "maxWarCultureLost")}` },
    { cat: "war", id: "peace_rate", icon: "☮️", title: "愛與和平・和平主義者", subtitle: "和平王（不宣戰也不侵略的場次比例最高）", metric: "peaceRate", unit: "%", descFn: (st) => `和平場次 ${st.peaceRate}% (${st.peaceGames}/${st.parsedGames})` },
    // 🗡️ 侵略與防守
    { cat: "aggr", id: "aggr_win_rate", icon: "🗡️", title: "趁火打劫・卑鄙之王", subtitle: "卑鄙王（場均侵略成功次數最高）", metric: "aggrWinRate", unit: "次/場", descFn: (st) => `場均侵略成功 ${st.aggrWinRate} 次 (共 ${st.aggrSucceeded} 次)` },
    { cat: "aggr", id: "aggr_in_game_max", icon: "🏴‍☠️", title: "海盜狂歡・單場掠奪王", subtitle: "單場侵略成功最多次", metric: "maxAggrWinsInGame", unit: "次", descFn: (st) => `單場侵略成功 ${st.maxAggrWinsInGame} 次${at(st, "maxAggrWinsInGame")}` },
    { cat: "aggr", id: "aggr_success_rate", icon: "🎯", title: "彈無虛發・神準侵略家", subtitle: `侵略成功率最高（需 ≥${MIN_AGGR_SAMPLES} 次侵略）`, metric: "aggrSuccessRate", unit: "%", eligibleFn: (st) => st.aggrDeclared >= MIN_AGGR_SAMPLES, descFn: (st) => `侵略成功率 ${st.aggrSuccessRate}% (${st.aggrSucceeded}/${st.aggrDeclared})` },
    { cat: "aggr", id: "aggr_victim_rate", icon: "🛡️", title: "全村沙包・可憐苦主", subtitle: "可憐王（場均被侵略成功次數最高）", metric: "aggrVictimRate", unit: "次/場", descFn: (st) => `場均被搶 ${st.aggrVictimRate} 次 (共 ${st.aggrSucceededOnMe} 次)` },
    { cat: "aggr", id: "defend_avg", icon: "🏰", title: "銅牆鐵壁・防守大師", subtitle: "防守王（場均成功擋下侵略最多次）", metric: "defendAvg", unit: "次/場", descFn: (st) => `場均擋下 ${st.defendAvg} 次侵略 (共 ${st.aggrDefended} 次)` },
    { cat: "aggr", id: "defend_in_game_max", icon: "🧱", title: "固若金湯・單場守城王", subtitle: "單場成功防禦侵略最多次", metric: "maxAggrDefendedInGame", unit: "次", descFn: (st) => `單場擋下 ${st.maxAggrDefendedInGame} 次侵略${at(st, "maxAggrDefendedInGame")}` },
    { cat: "aggr", id: "defend_success_rate", icon: "🛡️", title: "滴水不漏・防禦專家", subtitle: `侵略防禦成功率最高（需被侵略 ≥${MIN_AGGR_SAMPLES} 次）`, metric: "defendSuccessRate", unit: "%", eligibleFn: (st) => st.aggrOnMe >= MIN_AGGR_SAMPLES, descFn: (st) => `防禦成功率 ${st.defendSuccessRate}% (${st.aggrDefended}/${st.aggrOnMe})` },
    // 🕊️ 外交、政治與事件
    { cat: "politics", id: "pact_avg", icon: "🕊️", title: "縱橫捭闔・外交大師", subtitle: "外交大師（場均簽訂條約數最多）", metric: "pactAvg", unit: "份/場", descFn: (st) => `場均 ${st.pactAvg} 份條約 (共 ${st.pactsTotal} 份)` },
    { cat: "politics", id: "pact_break_avg", icon: "🔪", title: "翻臉不認人・背刺毀約王", subtitle: "毀約王（場均毀約最多次）", metric: "pactBreakAvg", unit: "次/場", descFn: (st) => `場均毀約 ${st.pactBreakAvg} 次 (共 ${st.pactsBrokenTotal} 次)` },
    { cat: "politics", id: "rev_avg", icon: "🚩", title: "赤色風暴・暴力革命家", subtitle: "革命家（場均發動革命最多次）", metric: "revAvg", unit: "次/場", descFn: (st) => `場均革命 ${st.revAvg} 次 (共 ${st.revolutionsTotal} 次)` },
    { cat: "politics", id: "peaceful_gov_avg", icon: "🌸", title: "和平演變・溫和改革家", subtitle: "改革王（場均和平更換政體最多次）", metric: "peacefulGovAvg", unit: "次/場", descFn: (st) => `場均和平換政體 ${st.peacefulGovAvg} 次 (共 ${st.peacefulGovTotal} 次)` },
    { cat: "politics", id: "gov_loyalty", icon: "💍", title: "從一而終・政體專情王", subtitle: "專情王（最常使用同一種政體的場次比例最高，不含獨裁）", metric: "govLoyalty", unit: "%", descFn: (st) => `${st.govLoyaltyName} ${st.govLoyalty}% (${st.govLoyaltyCount}/${st.parsedGames} 場)` },
    { cat: "politics", id: "event_most", icon: "📣", title: "唯恐天下不亂・事件狂", subtitle: "最愛打事件（場均打出事件最多張）", metric: "eventAvg", unit: "張/場", descFn: (st) => `場均打出 ${st.eventAvg} 張事件 (共 ${st.eventsPlayed} 張)` },
    { cat: "politics", id: "event_least", icon: "🤐", title: "惜牌如金・事件絕緣體", subtitle: "最不愛打事件（場均打出事件最少張）", metric: "eventAvg", unit: "張/場", asc: true, descFn: (st) => `場均只打出 ${st.eventAvg} 張事件 (共 ${st.eventsPlayed} 張)` },
    { cat: "politics", id: "impact_culture_max", icon: "🌍", title: "時代造局者・影響力之王", subtitle: "影響王（單一 Age III 影響事件最高分）", metric: "maxImpactCulture", unit: "分", descFn: (st) => `${st.maxImpactCulture} 分${st.maxImpactName ? " (" + st.maxImpactName + ")" : ""}${at(st, "maxImpactCulture")}` },
    // 🏛️ 建設與奇觀
    { cat: "build", id: "wonder_avg", icon: "🏛️", title: "鬼斧神工・奇觀建築師", subtitle: "奇觀王（場均完成奇觀數最多）", metric: "wonderAvg", unit: "座/場", descFn: (st) => `場均 ${st.wonderAvg} 座 (共 ${st.wondersTotal} 座)` },
    { cat: "build", id: "wonders_in_game_max", icon: "🏆", title: "奇觀大胃王・奇觀收集王", subtitle: "奇觀收集王（單場蓋過最多奇觀）", metric: "maxWondersInGame", unit: "座", descFn: (st) => `單場完成 ${st.maxWondersInGame} 座奇觀${at(st, "maxWondersInGame")}` },
    { cat: "build", id: "single_wonder_max", icon: "✨", title: "奇觀釘子戶・超級奇觀王", subtitle: "超級奇觀王（單一奇觀獲得最高分）", metric: "maxSingleWonderScore", unit: "分", descFn: (st) => `${st.maxSingleWonderScore} 分${st.maxSingleWonderName ? " (" + st.maxSingleWonderName + ")" : ""}${at(st, "maxSingleWonderScore")}` },
    { cat: "build", id: "urban_in_game_max", icon: "🏙️", title: "模擬城市玩家・城市建築王", subtitle: "城市建築王（同一時間城市建築工人最多，不含農場礦場）", metric: "maxUrbanInGame", unit: "人", descFn: (st) => `同時 ${st.maxUrbanInGame} 名工人在城市建築${at(st, "maxUrbanInGame")}` },
    { cat: "build", id: "colony_avg", icon: "⛵", title: "日不落帝國・大航海家", subtitle: "殖民王（場均殖民地最多）", metric: "colonyAvg", unit: "處/場", descFn: (st) => `場均 ${st.colonyAvg} 處殖民地 (共 ${st.coloniesTotal} 處)` },
    // 📈 產能與行動紀錄
    { cat: "production", id: "food_rate_max", icon: "🌾", title: "糧倉霸主・神農轉世", subtitle: "食物王（歷史最高單回合食物產量）", metric: "maxFoodRate", unit: "/回合", descFn: (st) => `最高單回合 +${st.maxFoodRate} 食物${at(st, "maxFoodRate")}` },
    { cat: "production", id: "res_rate_max", icon: "⛏️", title: "鋼鐵大亨・礦業巨頭", subtitle: "礦物王（歷史最高單回合礦物產量）", metric: "maxResRate", unit: "/回合", descFn: (st) => `最高單回合 +${st.maxResRate} 礦物${at(st, "maxResRate")}` },
    { cat: "production", id: "sci_rate_max", icon: "🔬", title: "科技瘋子・奇點降臨", subtitle: "科學王（歷史最高單回合科學產量）", metric: "maxSciRate", unit: "/回合", descFn: (st) => `最高單回合 +${st.maxSciRate} 科學${at(st, "maxSciRate")}` },
    { cat: "production", id: "cult_rate_max", icon: "🎨", title: "文藝復興巨匠・文化霸權", subtitle: "文化王（歷史最高單回合文化產量）", metric: "maxCultRate", unit: "/回合", descFn: (st) => `最高單回合 +${st.maxCultRate} 文化${at(st, "maxCultRate")}` },
    { cat: "production", id: "strength_max", icon: "🚀", title: "鋼鐵洪流・超級軍事王", subtitle: "超級軍事王（歷史最高武力值）", metric: "maxStrength", unit: "武力", descFn: (st) => `最高武力值 ${st.maxStrength}${at(st, "maxStrength")}` },
    { cat: "production", id: "ca_max", icon: "⏳", title: "時間管理大師・超級白點王", subtitle: "超級白點王（歷史最多內政點數）", metric: "maxCA", unit: "白點", descFn: (st) => `最高單回合 ${st.maxCA} 內政點${at(st, "maxCA")}` },
    { cat: "production", id: "ma_max", icon: "🎖️", title: "最高統帥・超級紅點王", subtitle: "超級紅點王（歷史最多軍事點數）", metric: "maxMA", unit: "紅點", descFn: (st) => `最高單回合 ${st.maxMA} 軍事點${at(st, "maxMA")}` },
    { cat: "production", id: "three_ca", icon: "🃏", title: "一擲千金・三白王", subtitle: "三白王（場均花 3 個白點拿牌最多次）", metric: "threeCAAvg", unit: "次/場", descFn: (st) => `場均 ${st.threeCAAvg} 次 (共 ${st.threeCATakes} 次)` },
    // 📦 庫存紀錄
    { cat: "stock", id: "food_stock_max", icon: "🥫", title: "囤糧末日派・食物庫存王", subtitle: "食物庫存王（同一時間擁有最多食物）", metric: "maxFoodStock", unit: "食物", descFn: (st) => `最高同時囤積 ${st.maxFoodStock} 食物${at(st, "maxFoodStock")}` },
    { cat: "stock", id: "res_stock_max", icon: "🪨", title: "守財奴・礦物庫存王", subtitle: "礦物庫存王（同一時間擁有最多礦物）", metric: "maxResStock", unit: "礦物", descFn: (st) => `最高同時囤積 ${st.maxResStock} 礦物${at(st, "maxResStock")}` },
    { cat: "stock", id: "sci_stock_max", icon: "📚", title: "學而不用・科學庫存王", subtitle: "科學庫存王（同一時間擁有最多科學）", metric: "maxSciStock", unit: "科學", descFn: (st) => `最高同時囤積 ${st.maxSciStock} 科學${at(st, "maxSciStock")}` },
    // 🎲 風格與災難
    { cat: "style", id: "female_rate", icon: "💗", title: "傾國傾城・後宮王", subtitle: "好色王（女性領袖使用率最高）", metric: "femaleRate", unit: "%", descFn: (st) => `後宮率 ${st.femaleRate}% (${st.femaleLeaders}/${st.leadersTotal})` },
    { cat: "style", id: "uprising_avg", icon: "💢", title: "血汗工廠・慣老闆暴動王", subtitle: "暴動王（場均人民暴動最多次）", metric: "uprisingAvg", unit: "次/場", descFn: (st) => `場均暴動 ${st.uprisingAvg} 次 (共 ${st.uprisingsTotal} 次)` },
    { cat: "style", id: "rats_max", icon: "🐀", title: "米奇妙妙屋・老鼠王", subtitle: "老鼠王（單場鼠疫被吃掉最多食物）", metric: "ratsFoodLostMax", unit: "食物", descFn: (st) => `單場被吃 ${st.ratsFoodLostMax} 食物${at(st, "ratsFoodLostMax")}` },
    { cat: "style", id: "dark_ages_max", icon: "📜", title: "焚書坑儒・焚書王", subtitle: "焚書王（單場黑暗時代被扣最多科學）", metric: "darkAgesSciLostMax", unit: "科學", descFn: (st) => `單場被扣 ${st.darkAgesSciLostMax} 科學${at(st, "darkAgesSciLostMax")}` }
  ];

  let awardsResult = [];
  let playerBadges = {};
  let playerAwardValues = {};
  PLAYERS.forEach(p => { playerBadges[p] = []; playerAwardValues[p] = []; });

  AWARD_DEFS.forEach(def => {
    let pool = eligiblePlayers.map(p => pf[p]).filter(st => !def.eligibleFn || def.eligibleFn(st));
    if (def.asc) pool = pool.filter(st => st.parsedGames > 0);
    pool.sort((a, b) => def.asc ? a[def.metric] - b[def.metric] : b[def.metric] - a[def.metric]);
    let topVal = pool.length > 0 ? pool[0][def.metric] : 0;
    let hasWinner = pool.length > 0 && (def.asc || topVal > 0);
    let winners = hasWinner ? pool.filter(x => x[def.metric] === topVal) : [];

    // Personal view: my value + my rank for every award
    PLAYERS.forEach(p => {
      let st = pf[p];
      let inPool = pool.indexOf(st) >= 0;
      let rank = null;
      if (inPool) {
        rank = 1 + pool.filter(x => def.asc ? x[def.metric] < st[def.metric] : x[def.metric] > st[def.metric]).length;
      }
      let reason = "";
      if (!inPool) reason = eligiblePlayers.indexOf(p) < 0 ? `需參與超過 ${RANK_THRESHOLD} 場` : "未達此項場次門檻";
      playerAwardValues[p].push({
        cat: def.cat, id: def.id, icon: def.icon, title: def.title, subtitle: def.subtitle,
        valueText: st.parsedGames > 0 ? def.descFn(st) : "尚無 Replay 數據",
        rank: rank, poolSize: pool.length, isTop: winners.indexOf(st) >= 0, reason: reason
      });
    });

    if (hasWinner) {
      let winnerNames = winners.map(w => w.name).join(" / ");
      let runnerUp = pool.find(x => x[def.metric] !== topVal);
      winners.forEach(w => {
        playerBadges[w.name].push({ id: def.id, icon: def.icon, title: def.title, subtitle: def.subtitle, valueText: def.descFn(w) });
      });
      awardsResult.push({
        cat: def.cat, id: def.id, icon: def.icon, title: def.title, subtitle: def.subtitle,
        winner: winnerNames, value: topVal, unit: def.unit,
        detail: winners.map(w => def.descFn(w)).join("；"),
        runnerUp: runnerUp ? `${runnerUp.name} (${runnerUp[def.metric]}${def.unit})` : "無"
      });
    } else {
      awardsResult.push({
        cat: def.cat, id: def.id, icon: def.icon, title: def.title, subtitle: def.subtitle,
        winner: def.eligibleFn ? "尚無人達到場次門檻" : "尚無人達成", value: 0, unit: def.unit,
        detail: def.eligibleFn ? "需達到場次門檻才列入" : "目前所有人都是 0", runnerUp: "-"
      });
    }
  });

  let globalAgeFavs = {};
  ["A", "I", "II", "III"].forEach(age => {
    globalAgeFavs[age] = { leader: getTopCountItem(globalLeadersByAge[age]), wonder: getTopCountItem(globalWondersByAge[age]) };
  });

  // ---- Data analysis ----
  const seatSummary = (arr) => arr.map(s => ({
    seat: s.seat, games: s.games,
    avgPoints: s.games > 0 ? parseFloat((s.points / s.games).toFixed(2)) : 0,
    firstPct: s.games > 0 ? parseFloat(((s.first / s.games) * 100).toFixed(1)) : 0,
    top2Pct: s.games > 0 ? parseFloat(((s.top2 / s.games) * 100).toFixed(1)) : 0
  }));
  const avg = (a) => a.length ? parseFloat((a.reduce((x, y) => x + y, 0) / a.length).toFixed(1)) : 0;
  // avg / max / min / sample standard deviation of one rank's totals
  const describe = (a) => {
    if (!a.length) return { n: 0, avg: 0, max: 0, min: 0, std: 0 };
    let m = a.reduce((x, y) => x + y, 0) / a.length;
    let v = a.length > 1 ? a.reduce((x, y) => x + (y - m) * (y - m), 0) / (a.length - 1) : 0;
    return { n: a.length, avg: parseFloat(m.toFixed(1)), max: Math.max.apply(null, a), min: Math.min.apply(null, a), std: parseFloat(Math.sqrt(v).toFixed(1)) };
  };
  let correlation = [
    { key: "food", label: "🌾 食物總產量（扣除消耗）" },
    { key: "res", label: "⛏️ 礦物總產量" },
    { key: "sci", label: "🔬 科學總產量" },
    { key: "pop", label: "👥 最後總人口" },
    { key: "ca", label: "⏳ 每回合行政點（白點）加總" },
    { key: "ma", label: "🎖️ 每回合軍事點（紅點）加總" }
  ].map(c => ({
    label: c.label,
    samples: corr[c.key].xs.length,
    r: pearson(corr[c.key].xs, corr[c.key].ys),
    avgByRank: [1, 2, 3, 4].map(rk => avg(corr[c.key].byRank[rk])),
    statsByRank: [1, 2, 3, 4].map(rk => describe(corr[c.key].byRank[rk]))
  }));

  let personalResult = {};
  let personalSeatResult = {};
  PLAYERS.forEach(p => {
    let st = pf[p];
    let pAgeFavs = {};
    ["A", "I", "II", "III"].forEach(age => {
      pAgeFavs[age] = { leader: getTopCountItem(st.leadersByAge[age]), wonder: getTopCountItem(st.wondersByAge[age]) };
    });
    let records = {
      maxWondersInGame: st.maxWondersInGame,
      maxImpactName: st.maxImpactName,
      maxSingleWonderName: st.maxSingleWonderName,
      ratsFoodLostTotal: st.ratsFoodLostTotal,
      darkAgesSciLostTotal: st.darkAgesSciLostTotal,
      uprisingsTotal: st.uprisingsTotal,
      at: st.at
    };
    REC_FIELDS.forEach(([k]) => { records[k] = st[k]; });
    personalResult[p] = {
      parsedGames: st.parsedGames,
      firstPlaceBadges: playerBadges[p],
      awardValues: playerAwardValues[p],
      hornyIndex: { pct: st.femaleRate, femaleCount: st.femaleLeaders, totalLeaders: st.leadersTotal },
      favoriteHarem: getTopCountItem(st.femaleLeaderCounts),
      favoriteGov: { top: getTopCountItem(st.govCounts), revolutions: st.revolutionsTotal, peaceful: st.peacefulGovTotal },
      ageFavorites: pAgeFavs,
      records: records
    };
    personalSeatResult[p] = seatSummary(personalSeats[p]);
  });

  return {
    leaderboard: {
      totalParsedGames: Object.keys(parsedGamesMap).length,
      categories: AWARD_CATEGORIES,
      awards: awardsResult,
      ageFavorites: globalAgeFavs,
      analysis: { seats: seatSummary(seats), correlation: correlation }
    },
    personal: personalResult,
    personalSeats: personalSeatResult   // contains season points -> only returned in the full view
  };
}

function doPost(e) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheets()[0];
  const data = JSON.parse(e.postData.contents);
  const gameId = String(data.gameId || "").trim().toUpperCase();
  if (!gameId) {
    return ContentService.createTextOutput(JSON.stringify({ status: "error", message: "Missing gameId" }))
      .setMimeType(ContentService.MimeType.JSON);
  }

  const r1 = data.r1 || "";
  const r2 = data.r2 || "";
  const r3 = data.r3 || "";
  const r4 = data.r4 || "";
  const timestamp = new Date();
  const rows = sheet.getDataRange().getValues();

  let foundIndex = -1;
  for (let i = 1; i < rows.length; i++) {
    if (String(rows[i][1] || "").trim().toUpperCase() === gameId) {
      foundIndex = i + 1;
      break;
    }
  }

  if (foundIndex > 0) {
    // Only overwrite r1..r4 if non-empty rankings are provided
    if (r1 && r2 && r3 && r4) {
      let existingDate = rows[foundIndex - 1][0] || timestamp;
      sheet.getRange(foundIndex, 1, 1, 6).setValues([[existingDate, gameId, r1, r2, r3, r4]]);
    }
  } else {
    sheet.appendRow([timestamp, gameId, r1, r2, r3, r4]);
  }

  // If detailed gameStats are uploaded by auto_collector.py, store in GameStats sheet tab
  if (data.gameStats) {
    const statsSheet = getOrCreateStatsSheet(ss);
    const sRows = statsSheet.getDataRange().getValues();
    let sFound = -1;
    for (let i = 1; i < sRows.length; i++) {
      if (String(sRows[i][1] || "").trim().toUpperCase() === gameId) {
        sFound = i + 1;
        break;
      }
    }
    const jsonStr = JSON.stringify({ players: data.gameStats });
    if (sFound > 0) {
      statsSheet.getRange(sFound, 1, 1, 7).setValues([[timestamp, gameId, r1, r2, r3, r4, jsonStr]]);
    } else {
      statsSheet.appendRow([timestamp, gameId, r1, r2, r3, r4, jsonStr]);
    }
  }

  return ContentService.createTextOutput(JSON.stringify({
    status: "success",
    gameId: gameId,
    queuedForReplayParser: !(r1 && r2 && r3 && r4)
  })).setMimeType(ContentService.MimeType.JSON);
}
