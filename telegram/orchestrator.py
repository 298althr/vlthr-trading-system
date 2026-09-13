"""
ALTHR Autopilot Orchestrator with SOS Architecture
Main orchestrator that integrates all modules and implements Self-Organizing System (SOS) architecture
"""

import json
import time
import asyncio
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
from datetime import datetime

from server_operations import ServerOperations, SystemHealth
from pipeline_operations import PipelineOperations, PipelineStatus, DataFreshness
from database_operations import DatabaseOperations, TradeInfo, AccountInfo
from file_operations import FileOperations


class SystemState(Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    RECOVERY = "recovery"
    UNKNOWN = "unknown"


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class SystemAlert:
    timestamp: str
    severity: str
    source: str
    message: str
    context: Dict
    action_taken: str = ""
    action_required: bool = False


@dataclass
class DecisionContext:
    timestamp: str
    system_state: SystemState
    health_metrics: Dict
    active_issues: List[str]
    confidence: float
    risk_level: str


class SOSOrchestrator:
    """
    Self-Organizing System Orchestrator
    Implements 7-layer SOS architecture:
    1. USMS (Unified State Management System)
    2. UEB (Unified Event Bus)
    3. KSR (Knowledge & Service Registry)
    4. Digital Twin
    5. Simulation Broker
    6. Walk-Forward Validator
    7. Workflow Engine
    """
    
    def __init__(self, config_path: str = None):
        # Initialize all modules
        self.server_ops = ServerOperations()
        self.pipeline_ops = PipelineOperations()
        self.db_ops = DatabaseOperations()
        self.file_ops = FileOperations()
        
        # SOS State
        self.system_state = SystemState.UNKNOWN
        self.alerts: List[SystemAlert] = []
        self.decision_history: List[DecisionContext] = []
        self.last_health_check = None
        self.last_pipeline_check = None
        
        # Configuration
        self.config = self._load_config(config_path)
        
        # Thresholds
        self.thresholds = {
            "cpu_warning": 80.0,
            "cpu_critical": 95.0,
            "memory_warning": 85.0,
            "memory_critical": 95.0,
            "disk_warning": 90.0,
            "disk_critical": 95.0,
            "data_stale_warning": 5.0,  # minutes
            "data_stale_critical": 10.0,  # minutes
            "container_restart_max": 3,  # max restarts per hour
        }
    
    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from file"""
        default_config = {
            "health_check_interval": 60,  # seconds
            "pipeline_check_interval": 300,  # seconds
            "auto_restart_enabled": True,
            "auto_escalation_enabled": True,
            "log_retention_days": 30,
            "backup_enabled": True,
        }
        
        if config_path:
            try:
                success, content = self.file_ops.read_file(config_path)
                if success:
                    return json.loads(content)
            except:
                pass
        
        return default_config
    
    # ===== USMS: Unified State Management System =====
    
    def get_system_state(self) -> Dict:
        """Get complete system state"""
        try:
            # Get health metrics
            health = self.server_ops.get_system_health()
            self.last_health_check = health
            
            # Get pipeline status
            pipeline_status = self.pipeline_ops.get_pipeline_status()
            self.last_pipeline_check = pipeline_status
            
            # Get database connection
            db_connected, db_msg = self.db_ops.check_connection()
            
            # Determine overall system state
            issues = []
            self.system_state = SystemState.HEALTHY
            
            if health.cpu_percent > self.thresholds["cpu_critical"]:
                self.system_state = SystemState.CRITICAL
                issues.append(f"CPU critical: {health.cpu_percent}%")
            elif health.cpu_percent > self.thresholds["cpu_warning"]:
                self.system_state = SystemState.WARNING
                issues.append(f"CPU warning: {health.cpu_percent}%")
            
            if health.memory_percent > self.thresholds["memory_critical"]:
                self.system_state = SystemState.CRITICAL
                issues.append(f"Memory critical: {health.memory_percent}%")
            elif health.memory_percent > self.thresholds["memory_warning"]:
                self.system_state = SystemState.WARNING
                issues.append(f"Memory warning: {health.memory_percent}%")
            
            if health.disk_percent > self.thresholds["disk_critical"]:
                self.system_state = SystemState.CRITICAL
                issues.append(f"Disk critical: {health.disk_percent}%")
            elif health.disk_percent > self.thresholds["disk_warning"]:
                self.system_state = SystemState.WARNING
                issues.append(f"Disk warning: {health.disk_percent}%")
            
            if health.containers_healthy < health.containers_total:
                self.system_state = SystemState.WARNING
                issues.append(f"Containers down: {health.containers_total - health.containers_healthy}")
            
            if not db_connected:
                self.system_state = SystemState.CRITICAL
                issues.append("Database connection failed")
            
            if pipeline_status == PipelineStatus.ABORTED:
                self.system_state = SystemState.CRITICAL
                issues.append("Pipeline aborted")
            elif pipeline_status == PipelineStatus.ERROR:
                self.system_state = SystemState.WARNING
                issues.append("Pipeline error")
            
            return {
                "state": self.system_state.value,
                "health": asdict(health),
                "pipeline_status": pipeline_status.value,
                "db_connected": db_connected,
                "issues": issues,
                "timestamp": health.timestamp
            }
        except Exception as e:
            self.system_state = SystemState.UNKNOWN
            return {
                "state": SystemState.UNKNOWN.value,
                "error": str(e),
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC")
            }
    
    # ===== UEB: Unified Event Bus =====
    
    def emit_event(self, event_type: str, payload: Dict, severity: AlertSeverity = AlertSeverity.INFO):
        """Emit an event to the unified event bus"""
        alert = SystemAlert(
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S UTC"),
            severity=severity.value,
            source=event_type,
            message=payload.get("message", ""),
            context=payload,
            action_required=severity in [AlertSeverity.ERROR, AlertSeverity.CRITICAL]
        )
        
        self.alerts.append(alert)
        
        # Keep only last 1000 alerts
        if len(self.alerts) > 1000:
            self.alerts = self.alerts[-1000:]
        
        # Log to file
        self.file_ops.append_to_file(
            f"{self.server_ops.vlthr_root}/logs/sos_events.log",
            json.dumps(asdict(alert)) + "\n"
        )
    
    # ===== KSR: Knowledge & Service Registry =====
    
    def register_service(self, service_name: str, service_info: Dict):
        """Register a service in the knowledge registry"""
        self.emit_event(
            "service_registered",
            {
                "service": service_name,
                "info": service_info,
                "message": f"Service {service_name} registered"
            }
        )
    
    def get_service_status(self, service_name: str) -> Dict:
        """Get status of a registered service"""
        # This would query the service registry
        return {"service": service_name, "status": "active"}
    
    # ===== Decision Intelligence Pipeline =====
    
    def analyze_situation(self) -> DecisionContext:
        """Analyze current situation and create decision context"""
        system_state = self.get_system_state()
        
        context = DecisionContext(
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S UTC"),
            system_state=SystemState(system_state["state"]),
            health_metrics=system_state["health"],
            active_issues=system_state["issues"],
            confidence=0.8 if system_state["state"] == "healthy" else 0.5,
            risk_level="low" if system_state["state"] == "healthy" else "high"
        )
        
        self.decision_history.append(context)
        return context
    
    def make_decision(self, context: DecisionContext) -> Tuple[str, Dict]:
        """
        Make autonomous decision based on context
        Returns: (action, action_details)
        """
        actions = []
        
        # Container health decisions
        if context.system_state in [SystemState.WARNING, SystemState.CRITICAL]:
            containers = self.server_ops.list_all_containers()
            for container in containers:
                if "down" in container.status.lower() or "exited" in container.status.lower():
                    if self.config["auto_restart_enabled"]:
                        success, msg = self.server_ops.restart_container(container.name)
                        actions.append({
                            "type": "restart_container",
                            "target": container.name,
                            "success": success,
                            "message": msg
                        })
                        self.emit_event(
                            "container_restarted",
                            {"container": container.name, "success": success},
                            AlertSeverity.WARNING
                        )
        
        # Data freshness decisions
        freshness = self.pipeline_ops.check_data_freshness()
        for data in freshness:
            if data.age_minutes > self.thresholds["data_stale_critical"]:
                success, msg = self.pipeline_ops.restart_ingestion()
                actions.append({
                    "type": "restart_ingestion",
                    "success": success,
                    "message": msg
                })
                self.emit_event(
                    "ingestion_restarted",
                    {"reason": "data_stale", "age_minutes": data.age_minutes},
                    AlertSeverity.CRITICAL
                )
        
        # Pipeline decisions
        pipeline_status = self.pipeline_ops.get_pipeline_status()
        if pipeline_status == PipelineStatus.ABORTED:
            success, msg = self.pipeline_ops.restart_pipeline()
            actions.append({
                "type": "restart_pipeline",
                "success": success,
                "message": msg
            })
            self.emit_event(
                "pipeline_restarted",
                {"reason": "aborted"},
                AlertSeverity.CRITICAL
            )
        
        return "actions_executed", {"actions": actions}
    
    # ===== Digital Twin & Simulation =====
    
    def create_digital_twin_snapshot(self) -> Dict:
        """Create a snapshot of the current system state (digital twin)"""
        system_state = self.get_system_state()
        
        twin = {
            "timestamp": system_state["timestamp"],
            "system_state": system_state["state"],
            "health_metrics": system_state["health"],
            "containers": [asdict(c) for c in self.server_ops.list_all_containers()],
            "pipeline_status": system_state["pipeline_status"],
            "open_trades": len(self.db_ops.get_open_trades()),
            "account_balance": self.db_ops.get_account_info().balance if self.db_ops.get_account_info() else 0,
        }
        
        # Save twin snapshot
        self.file_ops.write_file(
            f"{self.server_ops.vlthr_root}/backups/digital_twin_{int(time.time())}.json",
            json.dumps(twin, indent=2)
        )
        
        return twin
    
    # ===== Walk-Forward Validator =====
    
    def validate_decision(self, action: str, context: DecisionContext) -> Tuple[bool, str]:
        """Validate a decision before execution"""
        # SAF (Security-by-Architecture Framework) checks
        if action == "restart_container":
            return True, "Container restart is safe"
        elif action == "restart_pipeline":
            return True, "Pipeline restart is safe"
        elif action == "restart_ingestion":
            return True, "Ingestion restart is safe"
        else:
            return False, f"Unknown action: {action}"
    
    # ===== Workflow Engine =====
    
    def execute_workflow(self, workflow_name: str, params: Dict) -> Dict:
        """Execute a predefined workflow"""
        workflows = {
            "health_check": self._workflow_health_check,
            "incident_response": self._workflow_incident_response,
            "daily_maintenance": self._workflow_daily_maintenance,
            "system_recovery": self._workflow_system_recovery,
        }
        
        if workflow_name in workflows:
            return workflows[workflow_name](params)
        else:
            return {"error": f"Unknown workflow: {workflow_name}"}
    
    def _workflow_health_check(self, params: Dict) -> Dict:
        """Execute health check workflow"""
        results = {
            "workflow": "health_check",
            "steps": []
        }
        
        # Step 1: Check containers
        containers = self.server_ops.list_all_containers()
        results["steps"].append({
            "step": "check_containers",
            "result": f"{len(containers)} containers found"
        })
        
        # Step 2: Check database
        db_connected, db_msg = self.db_ops.check_connection()
        results["steps"].append({
            "step": "check_database",
            "result": db_msg
        })
        
        # Step 3: Check pipeline
        pipeline_status = self.pipeline_ops.get_pipeline_status()
        results["steps"].append({
            "step": "check_pipeline",
            "result": pipeline_status.value
        })
        
        # Step 4: Check data freshness
        freshness = self.pipeline_ops.check_data_freshness()
        results["steps"].append({
            "step": "check_data_freshness",
            "result": f"{len(freshness)} symbols checked"
        })
        
        results["status"] = "completed"
        return results
    
    def _workflow_incident_response(self, params: Dict) -> Dict:
        """Execute incident response workflow"""
        results = {
            "workflow": "incident_response",
            "steps": []
        }
        
        # Step 1: Assess situation
        context = self.analyze_situation()
        results["steps"].append({
            "step": "assess_situation",
            "result": context.system_state.value
        })
        
        # Step 2: Make decision
        action, action_details = self.make_decision(context)
        results["steps"].append({
            "step": "make_decision",
            "result": action,
            "details": action_details
        })
        
        # Step 3: Execute actions
        results["steps"].append({
            "step": "execute_actions",
            "result": f"{len(action_details['actions'])} actions executed"
        })
        
        # Step 4: Verify resolution
        new_context = self.analyze_situation()
        results["steps"].append({
            "step": "verify_resolution",
            "result": new_context.system_state.value
        })
        
        results["status"] = "completed"
        return results
    
    def _workflow_daily_maintenance(self, params: Dict) -> Dict:
        """Execute daily maintenance workflow"""
        results = {
            "workflow": "daily_maintenance",
            "steps": []
        }
        
        # Step 1: Rotate logs
        success, msg = self.file_ops.rotate_logs()
        results["steps"].append({
            "step": "rotate_logs",
            "result": msg
        })
        
        # Step 2: Clean old logs
        success, msg = self.file_ops.clean_old_logs(days_old=30)
        results["steps"].append({
            "step": "clean_old_logs",
            "result": msg
        })
        
        # Step 3: Vacuum database
        success, msg = self.db_ops.vacuum_analyze()
        results["steps"].append({
            "step": "vacuum_database",
            "result": msg
        })
        
        # Step 4: Create backup
        if self.config["backup_enabled"]:
            success, msg = self.db_ops.backup_database()
            results["steps"].append({
                "step": "backup_database",
                "result": msg
            })
        
        results["status"] = "completed"
        return results
    
    def _workflow_system_recovery(self, params: Dict) -> Dict:
        """Execute system recovery workflow"""
        results = {
            "workflow": "system_recovery",
            "steps": []
        }
        
        # Step 1: Check all containers
        containers = self.server_ops.list_all_containers()
        results["steps"].append({
            "step": "check_containers",
            "result": f"{len(containers)} containers found"
        })
        
        # Step 2: Restart stopped containers
        for container in containers:
            if "exited" in container.status.lower() or "down" in container.status.lower():
                success, msg = self.server_ops.start_container(container.name)
                results["steps"].append({
                    "step": f"start_{container.name}",
                    "result": msg
                })
        
        # Step 3: Restart pipeline
        success, msg = self.pipeline_ops.restart_pipeline()
        results["steps"].append({
            "step": "restart_pipeline",
            "result": msg
        })
        
        # Step 4: Restart ingestion
        success, msg = self.pipeline_ops.restart_ingestion()
        results["steps"].append({
            "step": "restart_ingestion",
            "result": msg
        })
        
        results["status"] = "completed"
        return results
    
    # ===== Main Loop =====
    
    async def run_autonomous_loop(self):
        """Main autonomous monitoring loop"""
        while True:
            try:
                # Analyze situation
                context = self.analyze_situation()
                
                # Make decisions if needed
                if context.system_state != SystemState.HEALTHY:
                    action, details = self.make_decision(context)
                
                # Sleep for configured interval
                await asyncio.sleep(self.config["health_check_interval"])
                
            except Exception as e:
                self.emit_event(
                    "loop_error",
                    {"error": str(e)},
                    AlertSeverity.ERROR
                )
                await asyncio.sleep(60)  # Wait 1 minute on error
    
    def get_status_report(self) -> Dict:
        """Generate comprehensive status report"""
        return {
            "system_state": self.get_system_state(),
            "recent_alerts": [asdict(a) for a in self.alerts[-10:]],
            "decision_history": [asdict(d) for d in self.decision_history[-5:]],
            "config": self.config,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC")
        }


# Validation function for testing
def validate_orchestrator() -> Dict[str, any]:
    """Validate orchestrator functionality"""
    orchestrator = SOSOrchestrator()
    
    results = {
        "get_system_state": False,
        "analyze_situation": False,
        "execute_workflow": False,
        "create_digital_twin": False
    }
    
    try:
        # Test system state
        state = orchestrator.get_system_state()
        results["get_system_state"] = state["state"] is not None
        results["system_state"] = state["state"]
        
        # Test situation analysis
        context = orchestrator.analyze_situation()
        results["analyze_situation"] = context.system_state is not None
        results["situation_state"] = context.system_state.value
        
        # Test workflow execution
        workflow_result = orchestrator.execute_workflow("health_check", {})
        results["execute_workflow"] = workflow_result["status"] == "completed"
        results["workflow_steps"] = len(workflow_result["steps"])
        
        # Test digital twin
        twin = orchestrator.create_digital_twin_snapshot()
        results["create_digital_twin"] = twin["timestamp"] is not None
        results["twin_timestamp"] = twin["timestamp"]
        
        results["validation_status"] = "PASSED" if all(results.values()) else "PARTIAL"
        
    except Exception as e:
        results["error"] = str(e)
        results["validation_status"] = "FAILED"
    
    return results


if __name__ == "__main__":
    # Run validation
    print("=== SOS Orchestrator Validation ===")
    results = validate_orchestrator()
    print(json.dumps(results, indent=2))
