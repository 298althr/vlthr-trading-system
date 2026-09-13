# Open-Source License Compliance

This file documents all third-party open-source frameworks and libraries used in the VLTHR Trading System.

## License Summary

The VLTHR Trading System is released under the **Business Source License 1.1 (BSL 1.1)**. This is a source-available license that:

- **Allows free use** for research, education, personal projects, and contributions back to this repository
- **Requires a commercial license** for production trading, SaaS deployment, or commercial product integration
- **Requires attribution** in all uses (retain notices, credit the project, link to the repository)
- **Converts to Apache 2.0** on 2030-09-13, making it fully open source at that point

See [LICENSE](LICENSE) and [ADDITIONAL_USE_GRANT.txt](ADDITIONAL_USE_GRANT.txt) for the full terms.

All dependencies listed below are compatible with BSL 1.1 licensed projects.

## Python Dependencies (Pipeline, Data Ingestion, Telegram)

| Package | Version | License | Purpose |
|--------|---------|---------|---------|
| pandas | >=1.5.0 | BSD-3-Clause | Data manipulation, OHLCV processing |
| numpy | >=1.23.0 | BSD-3-Clause | Numerical computation, ATR, indicators |
| psycopg2-binary | >=2.9.0 | LGPL-3.0 (dynamic linking) | PostgreSQL driver |
| scikit-learn | >=1.2.0 | BSD-3-Clause | ML gate model, regime clustering |
| river | >=0.19.0 | BSD-3-Clause | Online learning for gate model |
| hmmlearn | >=0.3.0 | BSD-3-Clause | Hidden Markov Model regime classification |
| websockets | >=12.0 | BSD-3-Clause | Bybit WebSocket data streaming |
| requests | >=2.28.0 | Apache-2.0 | HTTP API calls |

## Node.js Dependencies (Backend API)

| Package | Version | License | Purpose |
|--------|---------|---------|---------|
| express | ^4.21.2 | MIT | REST API server |
| pg | ^8.21.0 | MIT | PostgreSQL client |
| cors | ^2.8.5 | MIT | Cross-origin resource sharing |
| dotenv | ^17.4.2 | MIT | Environment variable loading |
| ws | ^8.18.0 | MIT | WebSocket server |

## Frontend Dependencies (React SPA)

| Package | Version | License | Purpose |
|--------|---------|---------|---------|
| react | ^19.2.6 | MIT | UI framework |
| react-dom | ^19.2.6 | MIT | React DOM renderer |
| vite | ^8.0.12 | MIT | Build tool and dev server |
| typescript | ~6.0.2 | Apache-2.0 | Type checking |
| lucide-react | ^1.17.0 | ISC | Icon library |
| @vitejs/plugin-react | ^6.0.1 | MIT | Vite React plugin |

## Infrastructure

| Software | License | Purpose |
|----------|---------|---------|
| PostgreSQL 15 / TimescaleDB | PostgreSQL License (BSD-like) | Time-series database |
| Redis 7 | BSD-3-Clause | Cache and pub/sub |
| Docker | Apache-2.0 | Container runtime |
| nginx | BSD-2-Clause | Frontend static file server |

## License Compatibility Notes

1. **psycopg2-binary** uses LGPL-3.0 with a dynamic linking exception. It is safe to use in a MIT-licensed project because the license applies to the library itself, not the consuming application, when dynamically linked.

2. **All other dependencies** use MIT, BSD-3-Clause, Apache-2.0, or ISC licenses, which are all permissive and compatible with MIT.

3. No GPL/AGPL-licensed code is used in this project.

## Open-Source Acknowledgments

This project builds on the work of the open-source community. We gratefully acknowledge:

- The **pandas** and **numpy** teams for the data processing foundation
- The **scikit-learn** and **river** teams for ML tooling
- The **React** and **Vite** teams for the frontend build system
- The **Express** team for the backend framework
- The **PostgreSQL** and **TimescaleDB** teams for the database
- The **Docker** team for containerization

---

For questions about license compliance, please open an issue with the `license` label.
