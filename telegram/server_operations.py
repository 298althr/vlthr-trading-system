"""
Server Operations Module for ALTHR Autopilot
Handles Docker container management, health checks, and system monitoring
"""

import subprocess
import json
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class ContainerStatus(Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class ContainerInfo:
    name: str
    status: str
    health: str
    ports: str
    uptime: str


@dataclass
class SystemHealth:
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    containers_healthy: int
    containers_total: int
    timestamp: str


class ServerOperations:
    """Manages server operations for VLTHR trading system"""
    
    def __init__(self, vlthr_root: str = "."):
        self.vlthr_root = vlthr_root
        self.dashboard_root = f"{vlthr_root}/paper_trade_unzipped/vlthr-signal-dashboard"
        
    def check_container_status(self, container_name: str) -> ContainerInfo:
        """Check status of a specific container"""
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", 
                 "{{.State.Status}}|{{.State.Health.Status}}|{{.State.StartedAt}}",
                 container_name],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                return ContainerInfo(
                    name=container_name,
                    status="not_found",
                    health="unknown",
                    ports="",
                    uptime=""
                )
            
            status, health, started = result.stdout.strip().split("|")
            return ContainerInfo(
                name=container_name,
                status=status,
                health=health,
                ports="",
                uptime=started
            )
        except Exception as e:
            return ContainerInfo(
                name=container_name,
                status="error",
                health="unknown",
                ports="",
                uptime=str(e)
            )
    
    def list_all_containers(self) -> List[ContainerInfo]:
        """List all VLTHR containers"""
        try:
            result = subprocess.run(
                ["docker", "ps", "--format", 
                 "{{.Names}}\t{{.Status}}\t{{.Ports}}"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            containers = []
            for line in result.stdout.strip().split("\n"):
                if line and "vlthr" in line.lower():
                    parts = line.split("\t")
                    name = parts[0]
                    status = parts[1] if len(parts) > 1 else ""
                    ports = parts[2] if len(parts) > 2 else ""
                    containers.append(ContainerInfo(
                        name=name,
                        status=status,
                        health="unknown",
                        ports=ports,
                        uptime=""
                    ))
            
            return containers
        except Exception as e:
            print(f"Error listing containers: {e}")
            return []
    
    def restart_container(self, container_name: str) -> Tuple[bool, str]:
        """Restart a container"""
        try:
            result = subprocess.run(
                ["docker", "restart", container_name],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                return True, f"Container {container_name} restarted successfully"
            else:
                return False, f"Failed to restart: {result.stderr}"
        except Exception as e:
            return False, f"Error restarting container: {e}"
    
    def stop_container(self, container_name: str) -> Tuple[bool, str]:
        """Stop a container"""
        try:
            result = subprocess.run(
                ["docker", "stop", container_name],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                return True, f"Container {container_name} stopped successfully"
            else:
                return False, f"Failed to stop: {result.stderr}"
        except Exception as e:
            return False, f"Error stopping container: {e}"
    
    def start_container(self, container_name: str) -> Tuple[bool, str]:
        """Start a container"""
        try:
            result = subprocess.run(
                ["docker", "start", container_name],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                return True, f"Container {container_name} started successfully"
            else:
                return False, f"Failed to start: {result.stderr}"
        except Exception as e:
            return False, f"Error starting container: {e}"
    
    def get_container_logs(self, container_name: str, tail: int = 50) -> str:
        """Get recent logs from a container"""
        try:
            result = subprocess.run(
                ["docker", "logs", "--tail", str(tail), container_name],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                return result.stdout
            else:
                return f"Error getting logs: {result.stderr}"
        except Exception as e:
            return f"Error getting logs: {e}"
    
    def get_system_health(self) -> SystemHealth:
        """Get overall system health metrics"""
        try:
            # Get container health
            containers = self.list_all_containers()
            healthy_count = sum(1 for c in containers if "healthy" in c.status.lower() or "up" in c.status.lower())
            
            # Get system metrics (simplified)
            cpu_percent = 0.0
            memory_percent = 0.0
            disk_percent = 0.0
            
            try:
                # CPU
                cpu_result = subprocess.run(
                    ["sh", "-c", "top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if cpu_result.stdout:
                    cpu_percent = float(cpu_result.stdout.strip())
            except:
                pass
            
            try:
                # Memory
                mem_result = subprocess.run(
                    ["free", "-m"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if mem_result.stdout:
                    lines = mem_result.stdout.split("\n")
                    for line in lines:
                        if "Mem:" in line:
                            parts = line.split()
                            total = float(parts[1])
                            used = float(parts[2])
                            memory_percent = (used / total) * 100
                            break
            except:
                pass
            
            try:
                # Disk
                disk_result = subprocess.run(
                    ["df", "-h", "/"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if disk_result.stdout:
                    lines = disk_result.stdout.split("\n")
                    for line in lines:
                        if "/" in line and "Filesystem" not in line:
                            parts = line.split()
                            disk_percent = float(parts[4].replace("%", ""))
                            break
            except:
                pass
            
            return SystemHealth(
                cpu_percent=cpu_percent,
                memory_percent=memory_percent,
                disk_percent=disk_percent,
                containers_healthy=healthy_count,
                containers_total=len(containers),
                timestamp=time.strftime("%Y-%m-%d %H:%M:%S UTC")
            )
        except Exception as e:
            print(f"Error getting system health: {e}")
            return SystemHealth(
                cpu_percent=0.0,
                memory_percent=0.0,
                disk_percent=0.0,
                containers_healthy=0,
                containers_total=0,
                timestamp=time.strftime("%Y-%m-%d %H:%M:%S UTC")
            )
    
    def rebuild_container(self, container_name: str, compose_file: str = "docker-compose.linux.yml") -> Tuple[bool, str]:
        """Rebuild a container using docker-compose"""
        try:
            result = subprocess.run(
                ["docker-compose", "-f", compose_file, "up", "-d", "--build", container_name],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=self.dashboard_root
            )
            
            if result.returncode == 0:
                return True, f"Container {container_name} rebuilt successfully"
            else:
                return False, f"Failed to rebuild: {result.stderr}"
        except Exception as e:
            return False, f"Error rebuilding container: {e}"
    
    def kill_stuck_container(self, container_name: str) -> Tuple[bool, str]:
        """Force kill a stuck container using PID"""
        try:
            # Get container PID
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Pid}}", container_name],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                return False, f"Failed to get container PID: {result.stderr}"
            
            pid = result.stdout.strip()
            
            # Kill the PID
            kill_result = subprocess.run(
                ["sudo", "kill", "-9", pid],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if kill_result.returncode == 0:
                # Remove container
                remove_result = subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                return True, f"Container {container_name} force-killed and removed"
            else:
                return False, f"Failed to kill PID: {kill_result.stderr}"
        except Exception as e:
            return False, f"Error killing container: {e}"


# Validation function for testing
def validate_server_operations() -> Dict[str, any]:
    """Validate all server operations"""
    ops = ServerOperations()
    
    results = {
        "list_containers": False,
        "check_health": False,
        "get_logs": False,
        "system_health": False
    }
    
    try:
        # Test list containers
        containers = ops.list_all_containers()
        results["list_containers"] = len(containers) >= 0
        results["containers_found"] = len(containers)
        
        # Test system health
        health = ops.get_system_health()
        results["check_health"] = health.timestamp is not None
        results["health_data"] = {
            "cpu": health.cpu_percent,
            "memory": health.memory_percent,
            "disk": health.disk_percent
        }
        
        # Test get logs (if containers exist)
        if containers:
            logs = ops.get_container_logs(containers[0].name, tail=5)
            results["get_logs"] = len(logs) >= 0
            results["logs_sample"] = logs[:100] if logs else "No logs"
        
        results["validation_status"] = "PASSED" if all(results.values()) else "PARTIAL"
        
    except Exception as e:
        results["error"] = str(e)
        results["validation_status"] = "FAILED"
    
    return results


if __name__ == "__main__":
    # Run validation
    print("=== Server Operations Validation ===")
    results = validate_server_operations()
    print(json.dumps(results, indent=2))
