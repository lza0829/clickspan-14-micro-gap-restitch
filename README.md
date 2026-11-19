# 点跨（Clickspan）

点击流装载 → 按访客空闲超时切会话 → 脏事件隔离 → 会话证明互证。

**不是** 运单轨迹时间线（途迹）、变更投影流（changecast）、CDC Sink、券核销钉扎、日结封账、双边对齐、改价价签、证据包放行。  
去皮后是：**事件装载 + 会话窗物化 + 闸/隔离 + 证明**。

## 启动

```powershell
# 后端
cd backend
pip install -r requirements.txt
$env:DATA_DIR = "$PWD\data"
$env:FIXTURES_DIR = "$PWD\..\fixtures"
uvicorn app.main:app --host 127.0.0.1 --port 10900

# 前端
cd frontend
npm install
npm run dev
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://127.0.0.1:5900 |
| 接口 | http://127.0.0.1:10900 |

## UI

**泳道织机**：顶栏品牌 + 顶栏菜单（织机 / 收流 / 搁置 / 核证 / 切窗）+ 横向会话串珠。  
不用底栏导航、不用三栏测绘板、不用左轨编号、不用流程 ribbon。

## 微间隙缝回（micro_gap_restitch）

切窗偶发把间隙 ≤ `micro_gap_sec` 的两段误切成两会话。织机页「扫描并缝回」把同访客
相邻误切对缝成单一对外会话：被吞会话标 `merged_away`、成员改挂保留会话、写缝回边
（`restitch_edges`：被吞 / 保留 / 微间隙秒），同事务完成、失败整体回滚。核证页只读
核对每条边与残留扫描。`micro_gap_sec` 在切窗页配置，须严格小于 `idle_timeout_sec`。

| 接口 | 说明 |
| --- | --- |
| `GET /api/restitch/candidates` | 扫描入口（只读）：当前误切候选对 |
| `POST /api/restitch/run` | 扫描入口（执行）：逐对事务缝回至收敛 |
| `GET /api/restitch/edges` | 缝回边审计（只读） |
| `GET /api/restitch/verify` | 缝回核证（只读） |
| `GET/PUT /api/restitch/config` | 微间隙秒配置 |
| `POST /api/restitch/seed-fixture` | 装载微间隙夹具（幂等） |

验收：`cd backend && python -m pytest tests/test_micro_gap_restitch.py -q`
（夹具 `fixtures/micro_gap_restitch.json` 造 45s 微间隙两段，缝回后一段串珠。）
