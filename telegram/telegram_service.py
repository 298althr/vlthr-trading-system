"""
Telegram Service for VLTHR 3-Bot Architecture
Handles HTTP API communication with 3 specialized Telegram bots
"""

import os
import requests
import json
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

from orchestrator import SOSOrchestrator
from pipeline_operations import PipelineOperations
from database_operations import DatabaseOperations
from server_operations import ServerOperations


class BotType(Enum):
    DATA = "data"
    TRADING = "trading"
    DEVOPS = "devops"


@dataclass
class TelegramMessage:
    chat_id: str
    text: str
    parse_mode: str = "HTML"
    disable_web_page_preview: bool = True


class TelegramService:
    """Telegram service for 3-bot architecture"""
    
    def __init__(self):
        # Bot tokens from environment
        self.data_bot_token = os.getenv("DATA_TELEGRAM_BOT_TOKEN", "")
        self.trading_bot_token = os.getenv("TRADER_TELEGRAM_BOT_TOKEN", "")
        self.devops_bot_token = os.getenv("DEVOPS_TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        
        # Bot usernames (must match actual Telegram usernames)
        self.data_bot_username = "Vlthr_Data_bot"
        self.trading_bot_username = "Vlthr_trader_bot"
        self.devops_bot_username = "Vlthr_devops_bot"
        
        # Initialize operations modules
        self.orchestrator = SOSOrchestrator()
        self.pipeline_ops = PipelineOperations()
        self.db_ops = DatabaseOperations()
        self.server_ops = ServerOperations()
        
        # Command registry
        self.commands = {
            self.data_bot_username: {
                "/start": self.cmd_start,
                "/freshness": self.cmd_data_freshness,
                "/ingestion": self.cmd_data_ingestion,
                "/gaps": self.cmd_data_gaps,
                "/alerts": self.cmd_data_alerts,
            },
            self.trading_bot_username: {
                "/start": self.cmd_start,
                "/trades": self.cmd_trading_trades,
                "/account": self.cmd_trading_account,
                "/signals": self.cmd_trading_signals,
                "/performance": self.cmd_trading_performance,
                "/close": self.cmd_trading_close,
                "/pause": self.cmd_trading_pause,
                "/resume": self.cmd_trading_resume,
            },
            self.devops_bot_username: {
                "/start": self.cmd_start,
                "/status": self.cmd_devops_status,
                "/containers": self.cmd_devops_containers,
                "/logs": self.cmd_devops_logs,
                "/errors": self.cmd_devops_errors,
                "/restart": self.cmd_devops_restart,
                "/config": self.cmd_devops_config,
            },
        }
    
    def send_message(self, bot_token: str, message: TelegramMessage) -> Tuple[bool, str]:
        """Send message via Telegram HTTP API"""
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                "chat_id": message.chat_id,
                "text": message.text,
                "parse_mode": message.parse_mode,
                "disable_web_page_preview": message.disable_web_page_preview
            }
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                return True, "Message sent successfully"
            else:
                return False, f"API error: {response.status_code} - {response.text}"
                
        except Exception as e:
            return False, f"Error sending message: {e}"
    
    def get_bot_token(self, bot_type: BotType) -> str:
        """Get token for specific bot type"""
        if bot_type == BotType.DATA:
            return self.data_bot_token
        elif bot_type == BotType.TRADING:
            return self.trading_bot_token
        elif bot_type == BotType.DEVOPS:
            return self.devops_bot_token
        return ""
    
    # ===== SHARED COMMANDS =====
    
    def cmd_start(self, args: List[str]) -> str:
        """Show all available commands and workflows"""
        lines = [
            "🤖 <b>VLTHR Telegram Bot - Available Commands</b>",
            "",
            "<b>📊 DATA BOT (@Vlthr_Data_bot)</b>",
            "Data ingestion monitoring and quality checks",
            "  /freshness  - Check data freshness for all symbols",
            "  /ingestion  - View recent ingestion logs",
            "  /gaps       - Check for data gaps",
            "  /alerts     - View data-related alerts",
            "",
            "<b>💼 TRADING BOT (@Vlthr_trader_bot)</b>",
            "Trade management and performance tracking",
            "  /trades     - Show current open positions",
            "  /account    - Check account balance and risk",
            "  /signals    - View active trading signals",
            "  /performance- Check trading metrics (7 days)",
            "  /close [id] - Manually close a trade",
            "  /pause      - Pause trading",
            "  /resume     - Resume trading",
            "",
            "<b>🔧 DEVOPS BOT (@Vlthr_devops_bot)</b>",
            "System health and container management",
            "  /status     - Overall system health",
            "  /containers - Docker container status",
            "  /logs [name]- View container logs",
            "  /errors     - Recent error log",
            "  /restart [name]- Restart a container",
            "  /config     - Show configuration",
            "",
            "<b>🔄 Available Workflows (via Orchestrator)</b>",
            "  health_check     - Comprehensive system health check",
            "  incident_response- Automated incident response",
            "  daily_maintenance- Daily maintenance tasks",
            "  system_recovery  - Full system recovery",
            "",
            "<b>💡 Usage Tips</b>",
            "• Address commands to specific bots: @bot_name /command",
            "• Example: @Vlthr_Data_bot /freshness",
            "• All bots respond in the same group chat",
            "• Periodic reports sent automatically",
        ]
        return "\n".join(lines)
    
    # ===== DATA BOT COMMANDS =====
    
    def cmd_data_freshness(self, args: List[str]) -> str:
        """Check data freshness"""
        try:
            freshness = self.pipeline_ops.check_data_freshness()
            
            lines = ["⏰ <b>Data Freshness</b>"]
            for data in freshness:
                status_emoji = "✅" if data.status == "fresh" else "⚠️"
                lines.append(f"{status_emoji} {data.symbol} ({data.timeframe}): {data.age_minutes:.1f}m old")
            
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Error checking freshness: {e}"
    
    def cmd_data_ingestion(self, args: List[str]) -> str:
        """Check ingestion status"""
        try:
            logs = self.pipeline_ops.get_ingestion_logs(tail=20)
            return f"📊 <b>Ingestion Logs (last 20)</b>\n<pre>{logs[-500:]}</pre>"
        except Exception as e:
            return f"❌ Error getting ingestion logs: {e}"
    
    def cmd_data_gaps(self, args: List[str]) -> str:
        """Check for data gaps"""
        try:
            gaps = self.pipeline_ops.check_data_gaps()
            if gaps:
                lines = ["🔍 <b>Data Gaps Detected</b>"]
                for symbol, gap_list in gaps.items():
                    lines.append(f"{symbol}: {len(gap_list)} gaps")
                return "\n".join(lines)
            else:
                return "✅ <b>No data gaps detected</b>"
        except Exception as e:
            return f"❌ Error checking gaps: {e}"
    
    def cmd_data_alerts(self, args: List[str]) -> str:
        """View data-related alerts"""
        try:
            errors = self.db_ops.get_recent_errors(limit=10)
            data_errors = [e for e in errors if "ingestion" in e.get("source", "").lower()]
            
            if data_errors:
                lines = ["⚠️ <b>Data Alerts (last 10)</b>"]
                for error in data_errors:
                    lines.append(f"{error.get('created_at')}: {error.get('error_msg')}")
                return "\n".join(lines)
            else:
                return "✅ <b>No data alerts</b>"
        except Exception as e:
            return f"❌ Error getting alerts: {e}"
    
    # ===== TRADING BOT COMMANDS =====
    
    def cmd_trading_trades(self, args: List[str]) -> str:
        """Show current positions"""
        try:
            trades = self.db_ops.get_open_trades()
            
            if trades:
                lines = [f"💼 <b>Open Trades ({len(trades)})</b>"]
                for trade in trades:
                    pnl_emoji = "🟢" if trade.pnl_usd >= 0 else "🔴"
                    lines.append(f"{pnl_emoji} #{trade.id} {trade.symbol} {trade.side} "
                               f"Entry: ${trade.entry_price:.2f} PnL: ${trade.pnl_usd:.2f}")
                return "\n".join(lines)
            else:
                return "💼 <b>No open trades</b>"
        except Exception as e:
            return f"❌ Error getting trades: {e}"
    
    def cmd_trading_account(self, args: List[str]) -> str:
        """Check account balance"""
        try:
            account = self.db_ops.get_account_info()
            if account:
                return f"💰 <b>Account Info</b>\n" \
                       f"Balance: ${account.balance:.2f}\n" \
                       f"Equity: ${account.equity:.2f}\n" \
                       f"Margin Used: ${account.margin_used:.2f}\n" \
                       f"Open Risk: {account.open_risk_pct:.1f}%"
            else:
                return "❌ Account info not available"
        except Exception as e:
            return f"❌ Error getting account: {e}"
    
    def cmd_trading_signals(self, args: List[str]) -> str:
        """See active signals"""
        try:
            signals = self.pipeline_ops.get_active_signals()
            if signals:
                lines = [f"🎯 <b>Active Signals ({len(signals)})</b>"]
                for signal in signals:
                    lines.append(f"{signal.get('symbol')}: {signal.get('confidence')} DQS")
                return "\n".join(lines)
            else:
                return "🎯 <b>No active signals</b>"
        except Exception as e:
            return f"❌ Error getting signals: {e}"
    
    def cmd_trading_performance(self, args: List[str]) -> str:
        """Check trading metrics"""
        try:
            # Get recent closed trades
            query = """
                SELECT COUNT(*) as total,
                       AVG(net_pnl_usd) as avg_pnl,
                       SUM(CASE WHEN net_pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN net_pnl_usd < 0 THEN 1 ELSE 0 END) as losses
                FROM paper_trades 
                WHERE status LIKE 'CLOSED%' 
                AND exit_time_utc > NOW() - INTERVAL '7 days'
            """
            result = self.db_ops._execute_query(query)
            
            if result.data:
                row = result.data[0]
                total = int(row.get('total', 0))
                avg_pnl = float(row.get('avg_pnl', 0))
                wins = int(row.get('wins', 0))
                losses = int(row.get('losses', 0))
                win_rate = (wins / total * 100) if total > 0 else 0
                
                return f"📈 <b>Performance (7 days)</b>\n" \
                       f"Trades: {total}\n" \
                       f"Avg PnL: ${avg_pnl:.2f}\n" \
                       f"Wins: {wins} | Losses: {losses}\n" \
                       f"Win Rate: {win_rate:.1f}%"
            else:
                return "📈 <b>No recent trades</b>"
        except Exception as e:
            return f"❌ Error getting performance: {e}"
    
    def cmd_trading_close(self, args: List[str]) -> str:
        """Manually close a trade"""
        if not args:
            return "❌ Usage: /close <trade_id>"
        
        try:
            trade_id = int(args[0])
            success, msg = self.db_ops.close_trade(trade_id, reason="TELEGRAM")
            if success:
                return f"✅ {msg}"
            else:
                return f"❌ {msg}"
        except ValueError:
            return "❌ Invalid trade ID"
        except Exception as e:
            return f"❌ Error closing trade: {e}"
    
    def cmd_trading_pause(self, args: List[str]) -> str:
        """Pause trading"""
        try:
            success, msg = self.db_ops.pause_trading()
            if success:
                return f"✅ {msg}"
            else:
                return f"❌ {msg}"
        except Exception as e:
            return f"❌ Error pausing: {e}"
    
    def cmd_trading_resume(self, args: List[str]) -> str:
        """Resume trading"""
        try:
            success, msg = self.db_ops.resume_trading()
            if success:
                return f"✅ {msg}"
            else:
                return f"❌ {msg}"
        except Exception as e:
            return f"❌ Error resuming: {e}"
    
    # ===== DEVOPS BOT COMMANDS =====
    
    def cmd_devops_status(self, args: List[str]) -> str:
        """Overall system health"""
        try:
            state = self.orchestrator.get_system_state()
            health = state.get("health", {})
            
            lines = ["🟢 <b>System Status</b>"]
            lines.append(f"State: {state.get('state', 'UNKNOWN').upper()}")
            lines.append(f"CPU: {health.get('cpu_percent', 0):.1f}%")
            lines.append(f"Memory: {health.get('memory_percent', 0):.1f}%")
            lines.append(f"Disk: {health.get('disk_percent', 0):.1f}%")
            lines.append(f"Containers: {health.get('containers_healthy', 0)}/{health.get('containers_total', 0)}")
            
            if state.get("issues"):
                lines.append("\n⚠️ <b>Issues:</b>")
                for issue in state.get("issues", []):
                    lines.append(f"  - {issue}")
            
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Error getting status: {e}"
    
    def cmd_devops_containers(self, args: List[str]) -> str:
        """Docker container status"""
        try:
            containers = self.server_ops.list_all_containers()
            
            lines = [f"📦 <b>Container Status ({len(containers)})</b>"]
            for container in containers:
                status_emoji = "✅" if "up" in container.status.lower() or "running" in container.status.lower() else "❌"
                lines.append(f"{status_emoji} {container.name}: {container.status}")
            
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Error getting containers: {e}"
    
    def cmd_devops_logs(self, args: List[str]) -> str:
        """Stream container logs"""
        if not args:
            return "❌ Usage: /logs <container_name>"
        
        try:
            container_name = args[0]
            logs = self.server_ops.get_container_logs(container_name, tail=50)
            return f"📋 <b>{container_name} logs (last 50)</b>\n<pre>{logs[-1000:]}</pre>"
        except Exception as e:
            return f"❌ Error getting logs: {e}"
    
    def cmd_devops_errors(self, args: List[str]) -> str:
        """Recent error log"""
        try:
            errors = self.db_ops.get_recent_errors(limit=10)
            
            if errors:
                lines = ["🚨 <b>Recent Errors (last 10)</b>"]
                for error in errors:
                    lines.append(f"{error.get('created_at')}: {error.get('source')} - {error.get('error_msg')}")
                return "\n".join(lines)
            else:
                return "✅ <b>No recent errors</b>"
        except Exception as e:
            return f"❌ Error getting errors: {e}"
    
    def cmd_devops_restart(self, args: List[str]) -> str:
        """Restart a container"""
        if not args:
            return "❌ Usage: /restart <container_name>"
        
        try:
            container_name = args[0]
            success, msg = self.server_ops.restart_container(container_name)
            if success:
                return f"✅ {msg}"
            else:
                return f"❌ {msg}"
        except Exception as e:
            return f"❌ Error restarting: {e}"
    
    def cmd_devops_config(self, args: List[str]) -> str:
        """Show configuration"""
        try:
            config = self.orchestrator.config
            lines = ["⚙️ <b>Configuration</b>"]
            for key, value in config.items():
                lines.append(f"{key}: {value}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Error getting config: {e}"
    
    # ===== COMMAND PROCESSING =====
    
    def process_command(self, bot_username: str, command: str, args: List[str]) -> str:
        """Process a command for a specific bot"""
        if bot_username not in self.commands:
            return f"❌ Unknown bot: {bot_username}"
        
        if command not in self.commands[bot_username]:
            return f"❌ Unknown command: {command}\nAvailable: {', '.join(self.commands[bot_username].keys())}"
        
        # Execute command
        handler = self.commands[bot_username][command]
        return handler(args)
    
    def send_alert(self, bot_type: BotType, message: str, chat_id: str = None) -> Tuple[bool, str]:
        """Send an alert via specific bot. Uses chat_id from incoming message if provided."""
        token = self.get_bot_token(bot_type)
        if not token:
            return False, f"No token configured for {bot_type.value} bot"
        
        # Use provided chat_id (from incoming message) or fall back to env
        target_chat_id = chat_id if chat_id else self.chat_id
        
        msg = TelegramMessage(
            chat_id=target_chat_id,
            text=message
        )
        
        return self.send_message(token, msg)
    
    # ===== PERIODIC REPORTING =====
    
    def generate_pipeline_report(self) -> str:
        """Generate pipeline performance report"""
        try:
            trace = self.db_ops.get_pipeline_trace(limit=20)
            
            lines = ["📊 <b>Pipeline Performance Report</b>"]
            lines.append(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
            lines.append("")
            
            if trace:
                lines.append("<b>Last 20 Pipeline Steps:</b>")
                for step in trace:
                    status_emoji = "✅" if step.get("status") == "OK" else "❌"
                    lines.append(f"{status_emoji} {step.get('node')}: {step.get('duration_ms')}ms")
            else:
                lines.append("No recent pipeline trace data")
            
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Error generating report: {e}"
    
    def generate_ingestion_report(self) -> str:
        """Generate data ingestion report"""
        try:
            freshness = self.pipeline_ops.check_data_freshness()
            
            lines = ["📥 <b>Data Ingestion Report</b>"]
            lines.append(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
            lines.append("")
            
            for data in freshness:
                status_emoji = "✅" if data.status == "fresh" else "⚠️"
                lines.append(f"{status_emoji} {data.symbol} ({data.timeframe}): {data.age_minutes:.1f}m old")
            
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Error generating report: {e}"
    
    def send_periodic_reports(self):
        """Send periodic reports to appropriate bots"""
        # Pipeline report via trading bot
        pipeline_report = self.generate_pipeline_report()
        self.send_alert(BotType.TRADING, pipeline_report)
        
        # Ingestion report via data bot
        ingestion_report = self.generate_ingestion_report()
        self.send_alert(BotType.DATA, ingestion_report)


# Validation function
def validate_telegram_service() -> Dict:
    """Validate Telegram service"""
    service = TelegramService()
    
    results = {
        "tokens_configured": False,
        "commands_registered": False,
        "test_data_freshness": False,
        "test_devops_status": False,
        "test_trading_trades": False
    }
    
    try:
        # Check tokens
        results["tokens_configured"] = all([
            service.data_bot_token,
            service.trading_bot_token,
            service.devops_bot_token,
            service.chat_id
        ])
        
        # Check commands
        total_commands = sum(len(cmds) for cmds in service.commands.values())
        results["commands_registered"] = total_commands > 0
        results["total_commands"] = total_commands
        
        # Test commands (without sending to Telegram)
        results["test_data_freshness"] = service.cmd_data_freshness([]) is not None
        results["test_devops_status"] = service.cmd_devops_status([]) is not None
        results["test_trading_trades"] = service.cmd_trading_trades([]) is not None
        
        results["validation_status"] = "PASSED" if all(results.values()) else "PARTIAL"
        
    except Exception as e:
        results["error"] = str(e)
        results["validation_status"] = "FAILED"
    
    return results


if __name__ == "__main__":
    print("=== Telegram Service Validation ===")
    results = validate_telegram_service()
    print(json.dumps(results, indent=2))
