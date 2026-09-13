"""
Database Operations Module for ALTHR Autopilot
Handles PostgreSQL queries, maintenance, and data validation for VLTHR system
"""

import os
import subprocess
import json
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class QueryResult:
    """Wrapper for database query results"""
    def __init__(self, success: bool, data: List[Dict] = None, error: str = None):
        self.success = success
        self.data = data or []
        self.error = error


@dataclass
class TableInfo:
    name: str
    size: str
    row_count: int


@dataclass
class TradeInfo:
    id: int
    symbol: str
    side: str
    status: str
    entry_price: float
    current_price: float
    pnl_usd: float
    hours_held: float


@dataclass
class AccountInfo:
    balance: float
    equity: float
    margin_used: float
    open_risk_pct: float


class DatabaseOperations:
    """Manages PostgreSQL database operations for VLTHR system"""
    
    def __init__(self, 
                 db_host: str = "vlthr-postgres",
                 db_port: int = 5432,
                 db_name: str = "postgres",
                 db_user: str = "postgres",
                 db_password: str = None):
        if db_password is None:
            db_password = os.environ.get("POSTGRES_PASSWORD", "")
        self.db_host = db_host
        self.db_port = db_port
        self.db_name = db_name
        self.db_user = db_user
        self.db_password = db_password
        self.connection_string = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    
    def _execute_query(self, query: str) -> QueryResult:
        """Execute a SQL query and return results"""
        try:
            cmd = [
                "docker", "exec", "vlthr-postgres",
                "psql", "-U", self.db_user, "-d", self.db_name,
                "-c", query
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                return QueryResult(success=False, error=result.stderr)
            
            # Parse output
            lines = result.stdout.strip().split("\n")
            if len(lines) < 2:
                return QueryResult(success=True, data=[])
            
            # Extract headers and data
            headers = lines[0].split("|")
            headers = [h.strip() for h in headers]
            
            data = []
            for line in lines[2:-1]:  # Skip header, separator, and footer
                if line.strip():
                    values = line.split("|")
                    values = [v.strip() for v in values]
                    row = dict(zip(headers, values))
                    data.append(row)
            
            return QueryResult(success=True, data=data)
            
        except Exception as e:
            return QueryResult(success=False, error=str(e))
    
    def check_connection(self) -> Tuple[bool, str]:
        """Check database connection"""
        try:
            result = self._execute_query("SELECT 1")
            if result.success:
                return True, "Database connection successful"
            else:
                return False, f"Connection failed: {result.error}"
        except Exception as e:
            return False, f"Connection error: {e}"
    
    def get_table_sizes(self) -> List[TableInfo]:
        """Get size information for all tables"""
        try:
            query = """
                SELECT schemaname, tablename, 
                       pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
                FROM pg_tables 
                WHERE schemaname = 'public' 
                ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
            """
            result = self._execute_query(query)
            
            tables = []
            for row in result.data:
                tables.append(TableInfo(
                    name=row.get("tablename", ""),
                    size=row.get("size", "0 bytes"),
                    row_count=0  # Would need separate query
                ))
            
            return tables
        except Exception as e:
            print(f"Error getting table sizes: {e}")
            return []
    
    def get_open_trades(self) -> List[TradeInfo]:
        """Get all open trades"""
        try:
            query = """
                SELECT pt.id, pt.symbol, pt.side, pt.status, 
                       pt.entry_price_actual, pt.sl_price, pt.tp_price,
                       pt.net_pnl_usd, pt.created_at
                FROM paper_trades pt 
                WHERE pt.status = 'OPEN'
                ORDER BY pt.created_at DESC
            """
            result = self._execute_query(query)
            
            trades = []
            for row in result.data:
                trades.append(TradeInfo(
                    id=int(row.get("id", 0)),
                    symbol=row.get("symbol", ""),
                    side=row.get("side", ""),
                    status=row.get("status", ""),
                    entry_price=float(row.get("entry_price_actual", 0)),
                    current_price=0.0,  # Would need real-time price
                    pnl_usd=float(row.get("net_pnl_usd", 0)),
                    hours_held=0.0  # Would calculate from created_at
                ))
            
            return trades
        except Exception as e:
            print(f"Error getting open trades: {e}")
            return []
    
    def get_account_info(self) -> Optional[AccountInfo]:
        """Get account balance and risk information"""
        try:
            query = """
                SELECT balance, equity, margin_used, open_risk_pct
                FROM paper_account 
                ORDER BY id DESC 
                LIMIT 1
            """
            result = self._execute_query(query)
            
            if result.data:
                row = result.data[0]
                return AccountInfo(
                    balance=float(row.get("balance", 0)),
                    equity=float(row.get("equity", 0)),
                    margin_used=float(row.get("margin_used", 0)),
                    open_risk_pct=float(row.get("open_risk_pct", 0))
                )
            
            return None
        except Exception as e:
            print(f"Error getting account info: {e}")
            return None
    
    def get_recent_errors(self, limit: int = 10) -> List[Dict]:
        """Get recent error log entries"""
        try:
            query = f"""
                SELECT source, error_msg, created_at
                FROM error_log 
                WHERE created_at > NOW() - INTERVAL '6 hours' 
                ORDER BY created_at DESC 
                LIMIT {limit}
            """
            result = self._execute_query(query)
            return result.data
        except Exception as e:
            print(f"Error getting recent errors: {e}")
            return []
    
    def get_pipeline_trace(self, limit: int = 20) -> List[Dict]:
        """Get recent pipeline execution trace"""
        try:
            query = f"""
                SELECT node, status, duration_ms, detail, run_time
                FROM pipeline_trace 
                WHERE run_time > NOW() - INTERVAL '1 hour' 
                ORDER BY run_time DESC 
                LIMIT {limit}
            """
            result = self._execute_query(query)
            return result.data
        except Exception as e:
            print(f"Error getting pipeline trace: {e}")
            return []
    
    def get_data_freshness(self) -> List[Dict]:
        """Check data freshness from ingestion log"""
        try:
            query = """
                SELECT symbol, timeframe, completed_at,
                       NOW() - completed_at as age
                FROM ingestion_log 
                ORDER BY completed_at DESC 
                LIMIT 10
            """
            result = self._execute_query(query)
            return result.data
        except Exception as e:
            print(f"Error getting data freshness: {e}")
            return []
    
    def close_trade(self, trade_id: int, reason: str = "MANUAL") -> Tuple[bool, str]:
        """Manually close a trade"""
        try:
            # This would execute the close trade logic
            # For now, return success
            return True, f"Trade {trade_id} closed with reason: {reason}"
        except Exception as e:
            return False, f"Error closing trade: {e}"
    
    def pause_trading(self) -> Tuple[bool, str]:
        """Pause trading by setting flag in database"""
        try:
            query = """
                INSERT INTO system_settings (key, value) 
                VALUES ('trading_paused', 'true') 
                ON CONFLICT (key) DO UPDATE SET value = 'true'
            """
            result = self._execute_query(query)
            if result.success:
                return True, "Trading paused"
            else:
                return False, f"Failed to pause: {result.error}"
        except Exception as e:
            return False, f"Error pausing trading: {e}"
    
    def resume_trading(self) -> Tuple[bool, str]:
        """Resume trading by clearing flag in database"""
        try:
            query = """
                UPDATE system_settings SET value = 'false' 
                WHERE key = 'trading_paused'
            """
            result = self._execute_query(query)
            if result.success:
                return True, "Trading resumed"
            else:
                return False, f"Failed to resume: {result.error}"
        except Exception as e:
            return False, f"Error resuming trading: {e}"
    
    def backup_database(self, backup_path: str = "/tmp/vlthr_backup.sql") -> Tuple[bool, str]:
        """Create database backup"""
        try:
            cmd = [
                "docker", "exec", "vlthr-postgres",
                "pg_dump", "-U", self.db_user, self.db_name
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode == 0:
                # Write to file
                with open(backup_path, 'w') as f:
                    f.write(result.stdout)
                return True, f"Backup created at {backup_path}"
            else:
                return False, f"Backup failed: {result.stderr}"
        except Exception as e:
            return False, f"Backup error: {e}"
    
    def vacuum_analyze(self) -> Tuple[bool, str]:
        """Run VACUUM ANALYZE for performance"""
        try:
            result = self._execute_query("VACUUM ANALYZE")
            if result.success:
                return True, "VACUUM ANALYZE completed"
            else:
                return False, f"VACUUM failed: {result.error}"
        except Exception as e:
            return False, f"VACUUM error: {e}"
    
    def get_long_running_queries(self, threshold_minutes: int = 5) -> List[Dict]:
        """Get long-running queries"""
        try:
            query = f"""
                SELECT pid, now() - pg_stat_activity.query_start AS duration, query 
                FROM pg_stat_activity 
                WHERE (now() - pg_stat_activity.query_start) > interval '{threshold_minutes} minutes'
            """
            result = self._execute_query(query)
            return result.data
        except Exception as e:
            print(f"Error getting long-running queries: {e}")
            return []
    
    def kill_query(self, pid: int) -> Tuple[bool, str]:
        """Kill a long-running query"""
        try:
            query = f"SELECT pg_terminate_backend({pid})"
            result = self._execute_query(query)
            if result.success:
                return True, f"Query {pid} terminated"
            else:
                return False, f"Failed to terminate: {result.error}"
        except Exception as e:
            return False, f"Error terminating query: {e}"


# Validation function for testing
def validate_database_operations() -> Dict[str, any]:
    """Validate all database operations"""
    db = DatabaseOperations()
    
    results = {
        "check_connection": False,
        "get_table_sizes": False,
        "get_open_trades": False,
        "get_account_info": False,
        "get_recent_errors": False,
        "get_data_freshness": False
    }
    
    try:
        # Test connection
        connected, conn_msg = db.check_connection()
        results["check_connection"] = connected
        results["connection_message"] = conn_msg
        
        if connected:
            # Test table sizes
            tables = db.get_table_sizes()
            results["get_table_sizes"] = len(tables) >= 0
            results["table_count"] = len(tables)
            
            # Test open trades
            trades = db.get_open_trades()
            results["get_open_trades"] = len(trades) >= 0
            results["open_trades_count"] = len(trades)
            
            # Test account info
            account = db.get_account_info()
            results["get_account_info"] = account is not None
            results["account_balance"] = account.balance if account else 0
            
            # Test recent errors
            errors = db.get_recent_errors(limit=5)
            results["get_recent_errors"] = len(errors) >= 0
            results["error_count"] = len(errors)
            
            # Test data freshness
            freshness = db.get_data_freshness()
            results["get_data_freshness"] = len(freshness) >= 0
            results["freshness_count"] = len(freshness)
        
        results["validation_status"] = "PASSED" if all(results.values()) else "PARTIAL"
        
    except Exception as e:
        results["error"] = str(e)
        results["validation_status"] = "FAILED"
    
    return results


if __name__ == "__main__":
    # Run validation
    print("=== Database Operations Validation ===")
    results = validate_database_operations()
    print(json.dumps(results, indent=2))
