import sqlite3
import json
import logging
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable
from contextlib import contextmanager
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Singleton instance holder
_db_instance = None
_db_lock = threading.Lock()

def get_database_instance(db_path: str = "price_adjustment.db", broadcast_callback: Optional[Callable] = None):
    """Get or create the singleton database instance"""
    global _db_instance
    
    if _db_instance is None:
        with _db_lock:
            if _db_instance is None:
                _db_instance = PriceAdjustmentDatabase(db_path, broadcast_callback)
                logger.info("✅ Created singleton database instance")
    
    # Update broadcast callback if provided and instance exists
    if broadcast_callback and _db_instance:
        _db_instance.set_broadcast_callback(broadcast_callback)
    
    return _db_instance

class PriceAdjustmentDatabase:
    """SQLite database handler for price adjustment system"""
    
    def __init__(self, db_path: str = "price_adjustment.db", broadcast_callback: Optional[Callable] = None):
        self.db_path = db_path
        self.lock = threading.Lock()
        self.broadcast_callback = broadcast_callback
        self._init_database()
    
    def set_broadcast_callback(self, callback: Callable):
        """Set the broadcast callback function after initialization"""
        self.broadcast_callback = callback
    
    def _init_database(self):
        """Initialize database tables"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Create adjustment_requests table (similar to submissions in appcertify-aiagent)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS adjustment_requests (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    request_data TEXT NOT NULL,
                    source TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT 'system',
                    status TEXT DEFAULT 'received',
                    adjustment_results TEXT,
                    completed_at TEXT
                )
            ''')
            
            # Add user_id column if it doesn't exist (for existing databases)
            try:
                cursor.execute('ALTER TABLE adjustment_requests ADD COLUMN user_id TEXT NOT NULL DEFAULT "system"')
                logger.info("✅ Added user_id column to adjustment_requests table")
            except Exception:
                # Column already exists or other error - continue silently
                pass
            
            # Create adjustment_logs table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS adjustment_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    agent_status TEXT NOT NULL,
                    results TEXT,
                    FOREIGN KEY (request_id) REFERENCES adjustment_requests (id)
                )
            ''')
            
            # Create documents table (simplified - just agreement_id and JSON)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    agreement_id TEXT NOT NULL UNIQUE,
                    agreement_json TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT 'system',
                    timestamp TEXT NOT NULL,
                    price_adjustment_completed BOOLEAN DEFAULT FALSE,
                    original_contract_value REAL,
                    adjusted_contract_value REAL,
                    inflation_rate_applied REAL,
                    adjustment_date TEXT,
                    bls_data_used TEXT,
                    effective_date TEXT,
                    expiration_date TEXT,
                    FOREIGN KEY (request_id) REFERENCES adjustment_requests (id)
                )
            ''')
            
            # Add user_id column to documents table if it doesn't exist
            try:
                cursor.execute('ALTER TABLE documents ADD COLUMN user_id TEXT NOT NULL DEFAULT "system"')
                logger.info("✅ Added user_id column to documents table")
            except Exception:
                # Column already exists or other error - continue silently
                pass
            
            # Add price adjustment columns if they don't exist
            adjustment_columns = [
                ('price_adjustment_completed', 'BOOLEAN DEFAULT FALSE'),
                ('original_contract_value', 'REAL'),
                ('adjusted_contract_value', 'REAL'),
                ('inflation_rate_applied', 'REAL'),
                ('adjustment_date', 'TEXT'),
                ('bls_data_used', 'TEXT'),
                ('effective_date', 'TEXT'),
                ('expiration_date', 'TEXT'),
                ('maestro_workflow_triggered', 'BOOLEAN DEFAULT FALSE'),
                ('maestro_response', 'TEXT'),
                ('maestro_triggered_at', 'TEXT')
            ]
            
            for column_name, column_def in adjustment_columns:
                try:
                    cursor.execute(f'ALTER TABLE documents ADD COLUMN {column_name} {column_def}')
                    logger.info(f"✅ Added {column_name} column to documents table")
                except Exception:
                    # Column already exists or other error - continue silently
                    pass
            
            # Create agreement_runs table (for tracking manual runs)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS agreement_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_date TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    agreements_found INTEGER NOT NULL,
                    agreements_processed INTEGER NOT NULL,
                    agreements_skipped INTEGER NOT NULL,
                    timestamp TEXT NOT NULL
                )
            ''')
            
            conn.commit()
            logger.info("✅ Database tables initialized successfully")
    
    @contextmanager
    def _get_connection(self):
        """Get database connection with proper locking"""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()
    
    def store_adjustment_request(self, request_id: str, request_data: Dict[Any, Any], source: str, user_id: str = "system") -> bool:
        """Store a new price adjustment request"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO adjustment_requests (id, timestamp, request_data, source, user_id)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    request_id,
                    datetime.utcnow().isoformat(),
                    json.dumps(request_data),
                    source,
                    user_id
                ))
                conn.commit()
                logger.info(f"✅ Stored adjustment request: {request_id} (user: {user_id})")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to store adjustment request: {e}")
            return False
    
    def get_adjustment_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Get adjustment request by ID"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM adjustment_requests WHERE id = ?', (request_id,))
                row = cursor.fetchone()
                
                if row:
                    return dict(row)
                return None
        except Exception as e:
            logger.error(f"❌ Failed to get adjustment request: {e}")
            return None
    
    def update_adjustment_request_status(self, request_id: str, status: str, results: Optional[Dict[Any, Any]] = None) -> bool:
        """Update adjustment request status and results"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                if results:
                    cursor.execute('''
                        UPDATE adjustment_requests 
                        SET status = ?, adjustment_results = ?, completed_at = ?
                        WHERE id = ?
                    ''', (
                        status,
                        json.dumps(results),
                        datetime.utcnow().isoformat(),
                        request_id
                    ))
                else:
                    cursor.execute('''
                        UPDATE adjustment_requests 
                        SET status = ?
                        WHERE id = ?
                    ''', (status, request_id))
                
                conn.commit()
                logger.info(f"✅ Updated adjustment request status: {request_id} -> {status}")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update adjustment request status: {e}")
            return False
    
    def log_agent_activity(self, request_id: str, agent_name: str, agent_status: str, results: Optional[Dict[Any, Any]] = None) -> bool:
        """Log activity from individual agents"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO adjustment_logs (request_id, timestamp, agent_name, agent_status, results)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    request_id,
                    datetime.utcnow().isoformat(),
                    agent_name,
                    agent_status,
                    json.dumps(results) if results else None
                ))
                conn.commit()
                logger.info(f"✅ Logged {agent_name} activity: {agent_status}")
                
                # Broadcast real-time update to dashboard using callback
                if self.broadcast_callback:
                    try:
                        detailed_message = self._create_detailed_message(agent_name, agent_status, results)
                        self.broadcast_callback(request_id, agent_name, detailed_message, results)
                        logger.debug(f"📡 Broadcast sent for {agent_name}: {detailed_message}")
                    except Exception as e:
                        logger.warning(f"Failed to broadcast update: {e}")
                else:
                    logger.debug("No broadcast callback configured")
                
                return True
        except Exception as e:
            logger.error(f"❌ Failed to log agent activity: {e}")
            return False
    
    def store_document(self, request_id: str, document_data: Dict[str, Any]) -> bool:
        """Store document information"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO documents (request_id, document_id, document_name, expiry_date, 
                                         current_value, adjusted_value, cpi_factor, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    request_id,
                    document_data.get('document_id'),
                    document_data.get('document_name'),
                    document_data.get('expiry_date'),
                    document_data.get('current_value'),
                    document_data.get('adjusted_value'),
                    document_data.get('cpi_factor'),
                    datetime.utcnow().isoformat()
                ))
                conn.commit()
                logger.info(f"✅ Stored document: {document_data.get('document_id')}")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to store document: {e}")
            return False
    
    def get_recent_adjustment_requests(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent adjustment requests"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM adjustment_requests 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                ''', (limit,))
                
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get recent adjustment requests: {e}")
            return []
    
    def get_agent_logs(self, request_id: str) -> List[Dict[str, Any]]:
        """Get agent logs for a specific request"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM adjustment_logs 
                    WHERE request_id = ? 
                    ORDER BY timestamp ASC
                ''', (request_id,))
                
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get agent logs: {e}")
            return []
    
    def get_requests_by_user(self, user_id: str, limit: int = 10) -> List[Dict]:
        """Get recent adjustment requests by user"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, timestamp, source, status, user_id, completed_at
                    FROM adjustment_requests 
                    WHERE user_id = ?
                    ORDER BY timestamp DESC 
                    LIMIT ?
                ''', (user_id, limit))
                
                requests = []
                for row in cursor.fetchall():
                    requests.append({
                        'request_id': row['id'],
                        'timestamp': row['timestamp'],
                        'source': row['source'],
                        'status': row['status'],
                        'user_id': row['user_id'],
                        'completed_at': row['completed_at']
                    })
                return requests
        except Exception as e:
            logger.error(f"❌ Failed to get requests by user: {e}")
            return []

    def store_agreement(self, request_id: str, agreement_data: Dict[str, Any], user_id: str = "system") -> bool:
        """Store agreement information with duplicate detection - simplified to just store ID and JSON"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Check if agreement already exists
                cursor.execute('''
                    SELECT id FROM documents WHERE agreement_id = ?
                ''', (agreement_data.get('id'),))
                
                if cursor.fetchone():
                    logger.info(f"⚠️ Agreement {agreement_data.get('id')} already exists in database - skipping")
                    return False
                
                # Store new agreement - just ID and full JSON with user_id
                cursor.execute('''
                    INSERT INTO documents (request_id, agreement_id, agreement_json, user_id, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    request_id,
                    agreement_data.get('id'),
                    json.dumps(agreement_data),
                    user_id,
                    datetime.utcnow().isoformat()
                ))
                conn.commit()
                logger.info(f"✅ Stored agreement: {agreement_data.get('id')} (user: {user_id})")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to store agreement: {e}")
            return False

    def log_agreement_run(self, user_id: str, found: int, processed: int, skipped: int) -> bool:
        """Log agreement processing run details"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO agreement_runs (
                        run_date, user_id, agreements_found, agreements_processed, 
                        agreements_skipped, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    datetime.now().strftime("%Y-%m-%d"),
                    user_id,
                    found,
                    processed,
                    skipped,
                    datetime.utcnow().isoformat()
                ))
                conn.commit()
                logger.info(f"✅ Logged agreement run for user {user_id}: {found} found, {processed} processed, {skipped} skipped")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to log agreement run: {e}")
            return False

    def get_processed_agreements(self) -> List[str]:
        """Get list of already processed agreement IDs"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT agreement_id FROM documents')
                rows = cursor.fetchall()
                return [row['agreement_id'] for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get processed agreements: {e}")
            return []

    def get_agreement_data(self, agreement_id: str) -> Dict[str, Any]:
        """Get full agreement data from JSON field"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT agreement_json FROM documents WHERE agreement_id = ?', (agreement_id,))
                row = cursor.fetchone()
                if row:
                    return json.loads(row['agreement_json'])
                return {}
        except Exception as e:
            logger.error(f"❌ Failed to get agreement data: {e}")
            return {}

    def get_unprocessed_agreements(self, request_id: str) -> List[Dict[str, Any]]:
        """Get agreements that haven't had price adjustment completed"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT agreement_id, agreement_json 
                    FROM documents 
                    WHERE request_id = ? AND (price_adjustment_completed IS NULL OR price_adjustment_completed = FALSE)
                    ORDER BY timestamp
                ''', (request_id,))
                rows = cursor.fetchall()
                
                agreements = []
                for row in rows:
                    agreement_data = json.loads(row['agreement_json'])
                    agreement_data['_db_agreement_id'] = row['agreement_id']  # Add DB reference
                    agreements.append(agreement_data)
                
                logger.info(f"📋 Found {len(agreements)} unprocessed agreements for request {request_id}")
                return agreements
        except Exception as e:
            logger.error(f"❌ Failed to get unprocessed agreements: {e}")
            return []

    def update_price_adjustment(self, agreement_id: str, adjustment_data: Dict[str, Any]) -> bool:
        """Update agreement with price adjustment details"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE documents SET
                        price_adjustment_completed = TRUE,
                        original_contract_value = ?,
                        adjusted_contract_value = ?,
                        inflation_rate_applied = ?,
                        adjustment_date = ?,
                        bls_data_used = ?,
                        effective_date = ?,
                        expiration_date = ?
                    WHERE agreement_id = ?
                ''', (
                    adjustment_data.get('original_contract_value'),
                    adjustment_data.get('adjusted_contract_value'),
                    adjustment_data.get('inflation_rate_applied'),
                    adjustment_data.get('adjustment_date'),
                    json.dumps(adjustment_data.get('bls_data_used', {})),
                    adjustment_data.get('effective_date'),
                    adjustment_data.get('expiration_date'),
                    agreement_id
                ))
                conn.commit()
                logger.info(f"✅ Updated price adjustment for agreement {agreement_id}")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update price adjustment: {e}")
            return False

    def update_maestro_workflow_status(self, agreement_id: str, maestro_response: str, maestro_success: bool, request_id: str = None) -> bool:
        """Update specific agreement with maestro workflow status"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Always update by agreement_id (specific agreement only)
                cursor.execute('''
                    UPDATE documents SET
                        maestro_workflow_triggered = ?,
                        maestro_response = ?,
                        maestro_triggered_at = ?
                    WHERE agreement_id = ?
                ''', (
                    maestro_success,
                    maestro_response,
                    datetime.utcnow().isoformat(),
                    agreement_id
                ))
                
                conn.commit()
                rows_affected = cursor.rowcount
                logger.info(f"✅ Updated maestro workflow status for agreement {agreement_id} (rows affected: {rows_affected})")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update maestro workflow status: {e}")
            return False

    def get_active_requests(self) -> List[Dict[str, Any]]:
        """Get requests that are currently active or processing"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM adjustment_requests 
                    WHERE status IN ('received', 'processing')
                    ORDER BY timestamp DESC
                    LIMIT 20
                ''')
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"❌ Failed to get active requests: {e}")
            return []

    def get_dashboard_stats(self) -> Dict[str, Any]:
        """Get statistics for the dashboard"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Total requests
                cursor.execute('SELECT COUNT(*) as count FROM adjustment_requests')
                total_requests = cursor.fetchone()['count']
                
                # Success rate
                cursor.execute('SELECT COUNT(*) as count FROM adjustment_requests WHERE status = "completed"')
                completed_requests = cursor.fetchone()['count']
                
                # Active requests
                cursor.execute('SELECT COUNT(*) as count FROM adjustment_requests WHERE status IN ("received", "processing")')
                active_requests = cursor.fetchone()['count']
                
                # Recent activity (last 24 hours)
                cursor.execute('''
                    SELECT COUNT(*) as count FROM adjustment_requests 
                    WHERE datetime(timestamp) > datetime('now', '-1 day')
                ''')
                recent_activity = cursor.fetchone()['count']
                
                # Agreement statistics
                cursor.execute('SELECT COUNT(*) as count FROM documents')
                total_agreements = cursor.fetchone()['count']
                
                cursor.execute('SELECT COUNT(*) as count FROM documents WHERE price_adjustment_completed = TRUE')
                processed_agreements = cursor.fetchone()['count']
                
                success_rate = (completed_requests / total_requests * 100) if total_requests > 0 else 0
                
                return {
                    'total_requests': total_requests,
                    'completed_requests': completed_requests,
                    'active_requests': active_requests,
                    'success_rate': round(success_rate, 1),
                    'recent_activity': recent_activity,
                    'total_agreements': total_agreements,
                    'processed_agreements': processed_agreements,
                    'timestamp': datetime.utcnow().isoformat()
                }
        except Exception as e:
            logger.error(f"❌ Failed to get dashboard stats: {e}")
            return {
                'total_requests': 0,
                'completed_requests': 0,
                'active_requests': 0,
                'success_rate': 0,
                'recent_activity': 0,
                'total_agreements': 0,
                'processed_agreements': 0,
                'timestamp': datetime.utcnow().isoformat()
            }

    def _create_detailed_message(self, agent_name: str, agent_status: str, results: Optional[Dict[Any, Any]] = None) -> str:
        """Create detailed messages for different agent activities"""
        try:
            # DocuSign Navigator messages
            if 'docusign' in agent_name.lower() or 'navigator' in agent_name.lower():
                if 'COMPLETE' in agent_status:
                    if results and 'expiring_agreements_found' in results:
                        count = results['expiring_agreements_found']
                        stored = results.get('agreements_stored', 0)
                        return f"DOCUSIGN_COMPLETE"
                    return "DOCUSIGN_COMPLETE"
                elif 'STARTED' in agent_status:
                    return "DOCUSIGN_NAVIGATOR_STARTED"
                elif 'FOUND' in agent_status:
                    return "AGREEMENTS_FOUND"
                
            # BLS Agent messages  
            elif 'bls' in agent_name.lower():
                if 'COMPLETE' in agent_status:
                    return "BLS_COMPLETE"
                elif 'STARTED' in agent_status:
                    return "BLS_STARTED"
                    
            # Maestro Agent messages
            elif 'maestro' in agent_name.lower():
                if 'TRIGGERED' in agent_status:
                    return "MAESTRO_TRIGGERED"
                elif 'STARTED' in agent_status:
                    return "MAESTRO_STARTED"
                    
            # Default message
            return f"{agent_name}: {agent_status}"
            
        except Exception as e:
            logger.error(f"Error creating detailed message: {e}")
            return f"{agent_name}: {agent_status}"
