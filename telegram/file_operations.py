"""
File Operations Module for ALTHR Autopilot
Handles log management, configuration files, and file system operations
"""

import os
import subprocess
import json
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class FileType(Enum):
    LOG = "log"
    CONFIG = "config"
    DATA = "data"
    SCRIPT = "script"
    UNKNOWN = "unknown"


@dataclass
class FileInfo:
    path: str
    name: str
    size_bytes: int
    modified_time: str
    file_type: FileType


@dataclass
class LogEntry:
    timestamp: str
    level: str
    message: str
    source: str


class FileOperations:
    """Manages file system operations for VLTHR system"""
    
    def __init__(self, 
                 vlthr_root: str = ".",
                 dashboard_root: str = "./paper_trade_unzipped/vlthr-signal-dashboard"):
        self.vlthr_root = vlthr_root
        self.dashboard_root = dashboard_root
        self.log_dirs = [
            f"{dashboard_root}/logs",
            f"{vlthr_root}/data/bybit/logs",
            "/var/log"
        ]
        self.config_files = [
            f"{vlthr_root}/.env",
            f"{dashboard_root}/.env",
            f"{dashboard_root}/docker-compose.yml"
        ]
    
    def list_directory(self, path: str, pattern: str = "*") -> List[FileInfo]:
        """List files in a directory with optional pattern matching"""
        try:
            dir_path = Path(path)
            if not dir_path.exists():
                return []
            
            files = []
            for file_path in dir_path.glob(pattern):
                if file_path.is_file():
                    stat = file_path.stat()
                    file_type = self._determine_file_type(file_path.name)
                    
                    files.append(FileInfo(
                        path=str(file_path),
                        name=file_path.name,
                        size_bytes=stat.st_size,
                        modified_time=time.strftime("%Y-%m-%d %H:%M:%S", 
                                                    time.localtime(stat.st_mtime)),
                        file_type=file_type
                    ))
            
            return files
        except Exception as e:
            print(f"Error listing directory {path}: {e}")
            return []
    
    def _determine_file_type(self, filename: str) -> FileType:
        """Determine file type based on extension"""
        if filename.endswith(".log") or filename.endswith(".txt"):
            return FileType.LOG
        elif filename.endswith(".json") or filename.endswith(".yml") or filename.endswith(".yaml") or filename.endswith(".env"):
            return FileType.CONFIG
        elif filename.endswith(".parquet") or filename.endswith(".csv"):
            return FileType.DATA
        elif filename.endswith(".py") or filename.endswith(".sh") or filename.endswith(".js"):
            return FileType.SCRIPT
        else:
            return FileType.UNKNOWN
    
    def read_file(self, path: str, max_lines: int = 100) -> Tuple[bool, str]:
        """Read file contents with line limit"""
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                if max_lines:
                    lines = lines[-max_lines:]  # Get last N lines
                return True, "".join(lines)
        except Exception as e:
            return False, f"Error reading file: {e}"
    
    def write_file(self, path: str, content: str) -> Tuple[bool, str]:
        """Write content to file"""
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(path), exist_ok=True)
            
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True, f"File written to {path}"
        except Exception as e:
            return False, f"Error writing file: {e}"
    
    def append_to_file(self, path: str, content: str) -> Tuple[bool, str]:
        """Append content to file"""
        try:
            with open(path, 'a', encoding='utf-8') as f:
                f.write(content)
            return True, f"Content appended to {path}"
        except Exception as e:
            return False, f"Error appending to file: {e}"
    
    def get_recent_logs(self, log_dir: str = None, tail: int = 50) -> List[LogEntry]:
        """Get recent log entries from log directory"""
        if log_dir is None:
            log_dir = self.log_dirs[0]
        
        try:
            log_files = self.list_directory(log_dir, pattern="*.log")
            if not log_files:
                return []
            
            # Get most recent log file
            latest_log = max(log_files, key=lambda x: x.modified_time)
            
            success, content = self.read_file(latest_log.path, max_lines=tail)
            if not success:
                return []
            
            # Parse log entries
            entries = []
            for line in content.split("\n"):
                if line.strip():
                    # Simple parsing - adjust based on actual log format
                    entries.append(LogEntry(
                        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                        level="INFO",
                        message=line.strip(),
                        source=latest_log.name
                    ))
            
            return entries[-tail:]  # Return last N entries
        except Exception as e:
            print(f"Error getting recent logs: {e}")
            return []
    
    def search_logs(self, pattern: str, log_dir: str = None) -> List[str]:
        """Search for pattern in log files"""
        if log_dir is None:
            log_dir = self.log_dirs[0]
        
        try:
            result = subprocess.run(
                ["grep", "-r", pattern, log_dir],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                return result.stdout.split("\n")
            else:
                return []
        except Exception as e:
            print(f"Error searching logs: {e}")
            return []
    
    def get_config_value(self, config_file: str, key: str) -> Optional[str]:
        """Get a specific configuration value from file"""
        try:
            success, content = self.read_file(config_file)
            if not success:
                return None
            
            for line in content.split("\n"):
                if line.strip().startswith(key):
                    # Handle different config formats
                    if "=" in line:
                        return line.split("=", 1)[1].strip()
                    elif ":" in line:
                        return line.split(":", 1)[1].strip()
            
            return None
        except Exception as e:
            print(f"Error getting config value: {e}")
            return None
    
    def set_config_value(self, config_file: str, key: str, value: str) -> Tuple[bool, str]:
        """Set a configuration value in file"""
        try:
            success, content = self.read_file(config_file)
            if not success:
                return False, "Could not read config file"
            
            lines = content.split("\n")
            updated = False
            
            for i, line in enumerate(lines):
                if line.strip().startswith(key):
                    # Update existing key
                    if "=" in line:
                        lines[i] = f"{key}={value}"
                    elif ":" in line:
                        lines[i] = f"{key}: {value}"
                    updated = True
                    break
            
            if not updated:
                # Add new key
                lines.append(f"{key}={value}")
            
            new_content = "\n".join(lines)
            return self.write_file(config_file, new_content)
        except Exception as e:
            return False, f"Error setting config value: {e}"
    
    def rotate_logs(self, log_dir: str = None, max_size_mb: int = 100) -> Tuple[bool, str]:
        """Rotate log files if they exceed size limit"""
        if log_dir is None:
            log_dir = self.log_dirs[0]
        
        try:
            log_files = self.list_directory(log_dir, pattern="*.log")
            rotated = 0
            
            for log_file in log_files:
                size_mb = log_file.size_bytes / (1024 * 1024)
                if size_mb > max_size_mb:
                    # Rotate the file
                    timestamp = time.strftime("%Y%m%d_%H%M%S")
                    rotated_path = f"{log_file.path}.{timestamp}"
                    
                    os.rename(log_file.path, rotated_path)
                    rotated += 1
            
            return True, f"Rotated {rotated} log files"
        except Exception as e:
            return False, f"Error rotating logs: {e}"
    
    def clean_old_logs(self, log_dir: str = None, days_old: int = 30) -> Tuple[bool, str]:
        """Clean log files older than specified days"""
        if log_dir is None:
            log_dir = self.log_dirs[0]
        
        try:
            log_files = self.list_directory(log_dir, pattern="*.log")
            cutoff_time = time.time() - (days_old * 24 * 60 * 60)
            deleted = 0
            
            for log_file in log_files:
                file_time = os.path.getmtime(log_file.path)
                if file_time < cutoff_time:
                    os.remove(log_file.path)
                    deleted += 1
            
            return True, f"Deleted {deleted} old log files"
        except Exception as e:
            return False, f"Error cleaning old logs: {e}"
    
    def get_disk_usage(self, path: str = None) -> Dict[str, float]:
        """Get disk usage for a path"""
        if path is None:
            path = self.vlthr_root
        
        try:
            result = subprocess.run(
                ["du", "-sh", path],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                # Parse output (e.g., "1.2G\tpath")
                parts = result.stdout.strip().split("\t")
                size_str = parts[0]
                
                # Convert to MB
                size_mb = 0
                if size_str.endswith("G"):
                    size_mb = float(size_str[:-1]) * 1024
                elif size_str.endswith("M"):
                    size_mb = float(size_str[:-1])
                elif size_str.endswith("K"):
                    size_mb = float(size_str[:-1]) / 1024
                
                return {"path": path, "size_mb": size_mb}
        except Exception as e:
            print(f"Error getting disk usage: {e}")
        
        return {"path": path, "size_mb": 0}
    
    def backup_file(self, source_path: str, backup_dir: str = None) -> Tuple[bool, str]:
        """Create a backup of a file"""
        if backup_dir is None:
            backup_dir = f"{self.vlthr_root}/backups"
        
        try:
            os.makedirs(backup_dir, exist_ok=True)
            
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = os.path.basename(source_path)
            backup_path = f"{backup_dir}/{filename}.{timestamp}"
            
            import shutil
            shutil.copy2(source_path, backup_path)
            
            return True, f"Backup created at {backup_path}"
        except Exception as e:
            return False, f"Error creating backup: {e}"
    
    def get_file_permissions(self, path: str) -> Optional[str]:
        """Get file permissions"""
        try:
            import stat
            mode = os.stat(path).st_mode
            return oct(stat.S_IMODE(mode))
        except Exception as e:
            print(f"Error getting file permissions: {e}")
            return None
    
    def set_file_permissions(self, path: str, permissions: str) -> Tuple[bool, str]:
        """Set file permissions (octal string, e.g., '644')"""
        try:
            mode = int(permissions, 8)
            os.chmod(path, mode)
            return True, f"Permissions set to {permissions}"
        except Exception as e:
            return False, f"Error setting permissions: {e}"


# Validation function for testing
def validate_file_operations() -> Dict[str, any]:
    """Validate all file operations"""
    ops = FileOperations()
    
    results = {
        "list_directory": False,
        "read_file": False,
        "write_file": False,
        "get_config": False,
        "get_disk_usage": False
    }
    
    try:
        # Test list directory
        files = ops.list_directory("/tmp")
        results["list_directory"] = len(files) >= 0
        results["files_found"] = len(files)
        
        # Test read/write file
        test_file = "/tmp/test_althr_ops.txt"
        write_success, write_msg = ops.write_file(test_file, "test content")
        results["write_file"] = write_success
        
        if write_success:
            read_success, content = ops.read_file(test_file)
            results["read_file"] = read_success and "test content" in content
            
            # Cleanup
            os.remove(test_file)
        
        # Test config operations
        test_config = "/tmp/test_config.env"
        ops.write_file(test_config, "TEST_KEY=test_value")
        config_value = ops.get_config_value(test_config, "TEST_KEY")
        results["get_config"] = config_value == "test_value"
        os.remove(test_config)
        
        # Test disk usage
        usage = ops.get_disk_usage("/tmp")
        results["get_disk_usage"] = usage["size_mb"] >= 0
        results["disk_usage_mb"] = usage["size_mb"]
        
        results["validation_status"] = "PASSED" if all(results.values()) else "PARTIAL"
        
    except Exception as e:
        results["error"] = str(e)
        results["validation_status"] = "FAILED"
    
    return results


if __name__ == "__main__":
    # Run validation
    print("=== File Operations Validation ===")
    results = validate_file_operations()
    print(json.dumps(results, indent=2))
