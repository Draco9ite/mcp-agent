// Price Adjustment Agent Dashboard JavaScript
class PriceAdjustmentDashboard {
    constructor() {
        this.socket = null;
        this.isConnected = false;
        this.init();
    }

    init() {
        this.setupWebSocket();
        this.setupEventListeners();
        this.loadInitialData();
    }

    setupWebSocket() {
        // Initialize Socket.IO connection
        this.socket = io();

        this.socket.on('connect', () => {
            console.log('🔌 Connected to Price Adjustment Agent');
            this.isConnected = true;
            this.updateConnectionStatus(true);
        });

        this.socket.on('disconnect', () => {
            console.log('🔌 Disconnected from server');
            this.isConnected = false;
            this.updateConnectionStatus(false);
        });

        this.socket.on('workflow_update', (data) => {
            console.log('📊 Workflow update received:', data);
            this.handleWorkflowUpdate(data);
        });
    }

    setupEventListeners() {
        // Form submission
        const form = document.getElementById('workflowForm');
        if (form) {
            form.addEventListener('submit', (e) => {
                e.preventDefault();
                this.triggerManualWorkflow();
            });
        }

        // Scheduled button (if exists)
        const scheduledBtn = document.getElementById('scheduledBtn');
        if (scheduledBtn) {
            scheduledBtn.addEventListener('click', (e) => {
                e.preventDefault();
                this.triggerScheduledWorkflow();
            });
        }

        // Auto-refresh toggle
        document.addEventListener('keydown', (e) => {
            if (e.key === 'r' && e.ctrlKey) {
                e.preventDefault();
                this.refreshDashboard();
            }
        });
    }

    updateConnectionStatus(connected) {
        const statusEl = document.getElementById('connectionStatus');
        if (statusEl) {
            if (connected) {
                statusEl.className = 'connection-status connected';
                statusEl.innerHTML = '<i class="fas fa-circle"></i><span>Connected</span>';
            } else {
                statusEl.className = 'connection-status disconnected';
                statusEl.innerHTML = '<i class="fas fa-circle"></i><span>Disconnected</span>';
            }
        }
    }

    async loadInitialData() {
        try {
            // Initialize dashboard
            this.updateWorkflowStatus('Ready');
            this.updateContractStats('-', '-', '-');  // Explicitly set all to dash
        } catch (error) {
            console.error('Error loading initial data:', error);
            this.updateWorkflowStatus('Error');
        }
    }

    async loadDashboardStats() {
        try {
            const response = await fetch('/api/stats');
            const data = await response.json();

            document.getElementById('totalRequests').textContent = data.total_requests || 0;
            document.getElementById('successRate').textContent = `${data.success_rate || 0}%`;
            document.getElementById('activeRequests').textContent = data.active_requests || 0;
            document.getElementById('processedAgreements').textContent = data.processed_agreements || 0;
        } catch (error) {
            console.error('Error loading stats:', error);
        }
    }

    async loadActiveRequests() {
        const loadingEl = document.getElementById('requestsLoading');
        const containerEl = document.getElementById('activeRequests');
        
        if (loadingEl) loadingEl.style.display = 'inline-block';
        
        try {
            const response = await fetch('/api/requests/active');
            const data = await response.json();

            if (data.active_requests && data.active_requests.length > 0) {
                containerEl.innerHTML = '';
                
                for (const request of data.active_requests) {
                    // Get detailed request info including agent logs
                    await this.renderRequestCard(request);
                }
            } else {
                containerEl.innerHTML = this.getEmptyStateHTML('No active requests');
            }
        } catch (error) {
            console.error('Error loading active requests:', error);
            containerEl.innerHTML = this.getEmptyStateHTML('Error loading requests');
        } finally {
            if (loadingEl) loadingEl.style.display = 'none';
        }
    }

    async renderRequestCard(request) {
        const containerEl = document.getElementById('activeRequests');
        
        try {
            // Get detailed request information
            const detailResponse = await fetch(`/api/requests/${request.id}/details`);
            const detailData = await detailResponse.json();
            
            const card = document.createElement('div');
            card.className = 'request-card';
            card.id = `request-${request.id}`;
            
            // Store request data for updates
            this.activeRequests.set(request.id, detailData.request);
            
            card.innerHTML = this.getRequestCardHTML(detailData.request, detailData.agent_logs);
            containerEl.appendChild(card);
            
        } catch (error) {
            console.error('Error rendering request card:', error);
        }
    }

    getRequestCardHTML(request, agentLogs = []) {
        const statusClass = this.getStatusClass(request.status);
        
        // Determine agent statuses from logs
        const agentStatuses = this.getAgentStatuses(agentLogs);
        
        return `
            <div class="request-header">
                <div class="request-id">${request.id}</div>
                <div class="status-badge ${statusClass}">${request.status}</div>
            </div>
            
            <div class="request-info" style="margin-bottom: 15px; color: #4a5568; font-size: 0.9rem;">
                <div><strong>User:</strong> ${request.user_id || 'system'}</div>
                <div><strong>Created:</strong> ${this.formatTimestamp(request.timestamp)}</div>
                <div><strong>Source:</strong> ${request.source || 'unknown'}</div>
            </div>
            
            <div class="agents-grid">
                ${this.getAgentCardsHTML(agentStatuses)}
            </div>
        `;
    }

    getAgentStatuses(agentLogs) {
        const agents = {
            'docusign_navigator': { name: 'Docusign Navigator', icon: 'fas fa-search', status: 'pending' },
            'bls_agent': { name: 'BLS Agent', icon: 'fas fa-chart-line', status: 'pending' },
            'maestro_agent': { name: 'Maestro Agent', icon: 'fas fa-cog', status: 'pending' }
        };

        // Update statuses based on logs
        for (const log of agentLogs) {
            const agentKey = log.agent_name.toLowerCase().replace('_agent', '').replace('agent', '');
            
            if (agents[agentKey] || agents[`${agentKey}_agent`]) {
                const key = agents[agentKey] ? agentKey : `${agentKey}_agent`;
                
                if (log.agent_status.includes('COMPLETE')) {
                    agents[key].status = 'completed';
                } else if (log.agent_status.includes('ERROR') || log.agent_status.includes('FAILED')) {
                    agents[key].status = 'error';
                } else {
                    agents[key].status = 'processing';
                }
            }
        }

        return agents;
    }

    getAgentCardsHTML(agentStatuses) {
        return Object.entries(agentStatuses).map(([key, agent]) => `
            <div class="agent-card ${agent.status}">
                <div class="agent-icon ${agent.status}">
                    <i class="${agent.icon}"></i>
                </div>
                <div class="agent-name">${agent.name}</div>
                <div class="agent-status">${this.formatAgentStatus(agent.status)}</div>
            </div>
        `).join('');
    }

    formatAgentStatus(status) {
        switch (status) {
            case 'completed': return '✅ Completed';
            case 'processing': return '⏳ Processing';
            case 'error': return '❌ Error';
            default: return '⏸️ Pending';
        }
    }

    getStatusClass(status) {
        switch (status?.toLowerCase()) {
            case 'processing': return 'status-processing';
            case 'completed': return 'status-completed';
            case 'failed':
            case 'error': return 'status-failed';
            default: return 'status-processing';
        }
    }

    handleWorkflowUpdate(data) {
        console.log('📊 Processing workflow update:', data);
        
        // Process the actual backend message
        this.processBackendMessage(data);
        
        // Show toast for important updates
        if (data.status && (data.status.includes('COMPLETE') || data.status.includes('ERROR'))) {
            this.showToast(`${data.agent} ${data.status.toLowerCase()}`, 
                          data.status.includes('ERROR') ? 'error' : 'success');
        }
    }

    processBackendMessage(data) {
        const message = data.message || data.status;
        const agent = data.agent;
        const details = data.data || {};
        
        if (agent === 'docusign_navigator_agent' || agent === 'docusign_navigator') {
            this.handleDocuSignActivity(message, details);
        } else if (agent === 'agreement_filter') {
            this.handleFilterActivity(message, details);
        } else if (agent === 'bls_agent') {
            this.handleBLSActivity(message, details);
        } else if (agent === 'maestro_agent') {
            this.handleMaestroActivity(message, details);
        } else if (agent === 'workflow') {
            // Handle workflow-level status updates
            this.handleWorkflowStatus(message, details);
        } else if (message && message.includes('NO_UNPROCESSED_AGREEMENTS')) {
            // Handle case when no agreements need processing
            this.handleNoUnprocessedAgreements(details);
        } else if (message && message.includes('WORKFLOW_PARTIAL')) {
            // Handle partial completion
            this.handlePartialCompletion(details);
        } else if (message && message.includes('WORKFLOW_SUCCESS')) {
            // Handle successful completion
            this.handleSuccessfulCompletion(details);
        }
    }

    handleFilterActivity(message, details) {
        if (message.includes('AGREEMENTS_FILTERED')) {
            const count = details.unprocessed_count || 0;
            this.updatePipelineStep(2, 'completed', `Filtered ${count} contracts for processing`);
            this.addStepDetail(2, `✅ Found ${count} agreements requiring price adjustment`, 'success');
            
            // List the agreements that need processing
            if (details.agreement_ids && details.agreement_ids.length > 0) {
                details.agreement_ids.forEach(id => {
                    this.addStepDetail(2, `📄 Agreement: ${id.substring(0, 8)}...`, 'info');
                });
                // Move to step 3 for processing
                this.updatePipelineStep(3, 'active', 'Starting price calculations...');
                this.updateWorkflowStatus('Calculating Adjustments');
            } else {
                // No agreements to process - mark steps 3 and 4 as skipped
                this.updatePipelineStep(3, 'skipped', 'No price calculations needed');
                this.addStepDetail(3, '⏭️ Skipped - No agreements to process', 'info');
                
                this.updatePipelineStep(4, 'skipped', 'No workflows to trigger');
                this.addStepDetail(4, '⏭️ Skipped - No agreements to process', 'info');
                
                this.updateWorkflowStatus('Completed - Nothing to Process');
                this.showToast('All agreements already processed. No further action needed.', 'info');
            }
        } else if (message.includes('NO_UNPROCESSED_AGREEMENTS')) {
            this.handleNoUnprocessedAgreements(details);
        }
    }

    handleNoUnprocessedAgreements(details) {
        // Update step 2 to show no agreements need processing
        this.updatePipelineStep(2, 'completed', 'No agreements require processing');
        this.addStepDetail(2, '✅ All agreements have already been processed', 'info');
        
        // Mark steps 3 and 4 as skipped since no processing is needed
        this.updatePipelineStep(3, 'skipped', 'No price calculations needed');
        this.addStepDetail(3, '⏭️ Skipped - No agreements to process', 'info');
        
        this.updatePipelineStep(4, 'skipped', 'No workflows to trigger');
        this.addStepDetail(4, '⏭️ Skipped - No agreements to process', 'info');
        
        // Update workflow status
        this.updateWorkflowStatus('Completed Successfully');
        
        // Show informative toast
        this.showToast('All agreements have already been processed. No further action needed.', 'success');
        
        // Update stats to show completion
        const found = document.getElementById('contractsFound').textContent;
        if (found !== '-') {
            this.updateContractStats(null, found, null);  // Set processed equal to found
        }
    }

    handleDocuSignActivity(message, details) {
        if (message.includes('DOCUSIGN_NAVIGATOR_STARTED')) {
            this.updatePipelineStep(1, 'active', 'Finding contracts...');
            this.updateWorkflowStatus('Extracting Contracts');
            this.addStepDetail(1, 'Docusign Navigator Agent started - searching for expiring agreements');
            
        } else if (message.includes('AGREEMENTS_FOUND')) {
            const count = details.expiring_agreements_found || 0;
            this.addStepDetail(1, `Found ${count} agreements expiring in next 30 days`);
            this.updateContractStats(count);
            
        } else if (message.includes('DOCUSIGN_COMPLETE')) {
            const found = details.expiring_agreements_found || 0;
            const stored = details.agreements_stored || 0;
            
            // Handle edge cases gracefully
            if (found === 0) {
                this.addStepDetail(1, '✅ No expiring agreements found in the next 30 days', 'info');
                this.updatePipelineStep(1, 'completed', 'No agreements to process');
                this.updateWorkflowStatus('No Agreements Found');
                
                // Show informative message
                setTimeout(() => {
                    this.showToast('No expiring agreements found in the next 30 days', 'info');
                }, 500);
                
            } else if (stored === 0 && found > 0) {
                // Agreements exist but weren't stored (already in DB)
                this.addStepDetail(1, `ℹ️ Found ${found} agreements already in database`, 'info');
                this.updatePipelineStep(1, 'completed', `${found} agreements found`);
                
                // Continue to step 2 to check if they need processing
                this.updatePipelineStep(2, 'active', 'Checking for unprocessed agreements...');
                this.updateWorkflowStatus('Checking Processing Status');
                
                // Don't show a warning - this is normal operation
                this.addStepDetail(2, 'Checking which agreements need price adjustment...', 'info');
                
            } else {
                // New agreements were stored
                this.addStepDetail(1, `Successfully stored ${stored} new agreements in database`);
                this.updatePipelineStep(1, 'completed', `Found & stored ${stored} agreements`);
                this.updatePipelineStep(2, 'active', 'Filtering contracts...');
                this.updateWorkflowStatus('Filtering Contracts');
                
                // Don't auto-advance past step 2 - let it show its own status
            }
        }
    }

    handleBLSActivity(message, details) {
        if (message.includes('BLS_STARTED')) {
            this.updatePipelineStep(3, 'active', 'Calculating inflation adjustments...');
            this.updateWorkflowStatus('Calculating Adjustments');
            this.addStepDetail(3, 'BLS Agent started - fetching CPI data and calculating adjustments');
            
        } else if (message.includes('BLS_COMPLETE')) {
            const agreementId = details.agreement_id || 'unknown';
            const originalValue = details.original_value || 0;
            const adjustedValue = details.adjusted_value || 0;
            const inflationRate = details.inflation_rate || 0;
            
            // Update step 3 progress
            this.addStepDetail(3, `Agreement ${agreementId.substring(0, 8)}: $${originalValue.toFixed(2)} → $${adjustedValue.toFixed(2)} (${inflationRate.toFixed(2)}% inflation)`);
            
            // Update processed count and total adjustment
            this.updateProcessedCount();
            this.updateTotalAdjustment(adjustedValue - originalValue);
            
            // Mark step 3 as completed after processing agreements
            setTimeout(() => {
                this.updatePipelineStep(3, 'completed', 'Price calculations completed');
                this.updateWorkflowStatus('Starting Workflows');
            }, 500);
        }
    }

    handleMaestroActivity(message, details) {
        if (message.includes('MAESTRO_STARTED')) {
            this.updatePipelineStep(4, 'active', 'Triggering Docusign workflows...');
            this.updateWorkflowStatus('Triggering Workflows');
            this.addStepDetail(4, 'Maestro Agent started - triggering Docusign workflows');
            
        } else if (message.includes('MAESTRO_TRIGGERED')) {
            const agreementId = details.agreement_id || 'unknown';
            this.addStepDetail(4, `Workflow triggered for agreement ${agreementId.substring(0, 8)}`);
            
            // Don't check completion here - wait for final status from backend
        }
    }

    updateProcessedCount() {
        const currentProcessed = document.getElementById('contractsProcessed').textContent;
        const newCount = currentProcessed === '-' ? 1 : parseInt(currentProcessed) + 1;
        this.updateContractStats(null, newCount, null);  // Only update processed count
    }

    updateTotalAdjustment(adjustmentAmount) {
        const currentTotal = document.getElementById('totalAdjustment').textContent;
        if (currentTotal === '-') {
            this.updateContractStats(null, null, `$${adjustmentAmount.toFixed(2)}`);  // Only update total
        } else {
            const current = parseFloat(currentTotal.replace('$', ''));
            const newTotal = current + adjustmentAmount;
            this.updateContractStats(null, null, `$${newTotal.toFixed(2)}`);  // Only update total
        }
    }

    checkWorkflowCompletion() {
        // This is now only called as a fallback if no explicit status is received
        setTimeout(() => {
            // Default to success if we got here without errors
            this.updatePipelineStep(4, 'completed', 'All workflows triggered successfully');
            this.updateWorkflowStatus('Completed Successfully');
        }, 2000);
    }

    handleSuccessfulCompletion(details) {
        this.updatePipelineStep(4, 'completed', 'All workflows triggered successfully');
        this.updateWorkflowStatus('Completed Successfully');
        this.showToast('Workflow completed successfully!', 'success');
    }

    updatePipelineStep(stepNumber, status, statusText) {
        const step = document.getElementById(`step${stepNumber}`);
        const stepStatus = document.getElementById(`step${stepNumber}-status`);
        
        if (step && stepStatus) {
            // Remove all status classes
            step.className = 'pipeline-step';
            step.classList.add(status);
            
            // Update status text and icon
            let icon = 'fas fa-circle';
            if (status === 'active') {
                icon = 'fas fa-spinner';
            } else if (status === 'completed') {
                icon = 'fas fa-check';
            } else if (status === 'error') {
                icon = 'fas fa-times';
            } else if (status === 'skipped') {
                icon = 'fas fa-forward';
            } else if (status === 'warning') {
                icon = 'fas fa-exclamation-triangle';
            }
            
            stepStatus.innerHTML = `<i class="${icon}"></i><span>${statusText}</span>`;
            
            // Show details section if there's activity
            if (status === 'active' || status === 'completed' || status === 'skipped' || status === 'warning') {
                const details = document.getElementById(`step${stepNumber}-details`);
                if (details) {
                    details.classList.add('show');
                }
            }
        }
    }

    addStepDetail(stepNumber, message, type = 'default') {
        const details = document.getElementById(`step${stepNumber}-details`);
        if (details) {
            const detailItem = document.createElement('div');
            detailItem.className = 'detail-item';
            
            // Add type-specific styling
            if (type === 'warning') {
                detailItem.style.borderLeftColor = '#ed8936';
                detailItem.style.backgroundColor = '#fffaf0';
            } else if (type === 'error') {
                detailItem.style.borderLeftColor = '#f56565';
                detailItem.style.backgroundColor = '#fff5f5';
            } else if (type === 'info') {
                detailItem.style.borderLeftColor = '#4299e1';
                detailItem.style.backgroundColor = '#ebf8ff';
            } else if (type === 'success') {
                detailItem.style.borderLeftColor = '#48bb78';
                detailItem.style.backgroundColor = '#f0fff4';
            }
            
            detailItem.textContent = message;
            details.appendChild(detailItem);
            
            // Scroll to show the new detail
            detailItem.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    }



    updateWorkflowStatus(status) {
        const statusElement = document.getElementById('currentStatus');
        if (statusElement) {
            statusElement.textContent = status;
        }
    }

    updateContractStats(found = null, processed = null, totalAdjustment = null) {
        const contractsFound = document.getElementById('contractsFound');
        const contractsProcessed = document.getElementById('contractsProcessed');
        const totalAdjustmentEl = document.getElementById('totalAdjustment');
        
        // Only update fields that are provided (not null)
        if (contractsFound && found !== null) contractsFound.textContent = found;
        if (contractsProcessed && processed !== null) contractsProcessed.textContent = processed;
        if (totalAdjustmentEl && totalAdjustment !== null) totalAdjustmentEl.textContent = totalAdjustment;
    }

    extractNumber(text) {
        const match = text.match(/(\d+)/);
        return match ? parseInt(match[1]) : 0;
    }

    resetPipeline() {
        // Reset all pipeline steps to waiting state
        for (let i = 1; i <= 4; i++) {
            this.updatePipelineStep(i, 'waiting', 'Waiting');
            const details = document.getElementById(`step${i}-details`);
            if (details) {
                details.innerHTML = '';
                details.classList.remove('show');
            }
        }
        
        // Reset status summary
        this.updateWorkflowStatus('Ready');
        this.updateContractStats('-', '-', '-');  // Explicitly set all to dash
    }

    async triggerManualWorkflow() {
        const userIdInput = document.getElementById('userId');
        const triggerBtn = document.getElementById('triggerBtn');
        
        if (!userIdInput.value.trim()) {
            this.showToast('Please enter a User ID', 'error');
            return;
        }

        // Disable input and button during processing
        userIdInput.disabled = true;
        if (triggerBtn) {
            triggerBtn.disabled = true;
            triggerBtn.textContent = 'Processing...';
        }

        // Reset pipeline to waiting state
        this.resetPipeline();

        const originalText = triggerBtn.innerHTML;
        

        try {
            const response = await fetch('/webhook/manual', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    user_id: userIdInput.value.trim()
                }),
            });

            const data = await response.json();

            if (response.ok) {
                this.showToast(`Workflow started! Request ID: ${data.request_id}`, 'success');
                
                // Clear the input
                userIdInput.value = '';
                
                // Update step 1 to show it's starting
                this.updatePipelineStep(1, 'active', 'Initializing...');
                this.updateWorkflowStatus('Starting...');
                
            } else {
                this.showToast(`Error: ${data.error}`, 'error');
                this.updateWorkflowStatus('Error');
            }
        } catch (error) {
            console.error('Error triggering workflow:', error);
            this.showToast('Failed to trigger workflow', 'error');
            this.updateWorkflowStatus('Error');
        } finally {
            triggerBtn.innerHTML = originalText;
            triggerBtn.disabled = false;
            userIdInput.disabled = false; // Re-enable input
        }
    }

    async triggerScheduledWorkflow() {
        const scheduledBtn = document.getElementById('scheduledBtn');
        
        // Disable input and button during processing
        if (scheduledBtn) {
            scheduledBtn.disabled = true;
            scheduledBtn.textContent = 'Processing...';
        }

        // Reset pipeline
        this.resetPipeline();
        
        const originalText = scheduledBtn.innerHTML;
        

        try {
            const response = await fetch('/webhook/scheduled', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({}),
            });

            const data = await response.json();

            if (response.ok) {
                this.showToast(`Scheduled workflow started! Request ID: ${data.request_id}`, 'success');
                this.updatePipelineStep(1, 'active', 'Initializing...');
                this.updateWorkflowStatus('Starting...');
            } else {
                this.showToast(`Error: ${data.error}`, 'error');
                this.updateWorkflowStatus('Error');
            }
        } catch (error) {
            console.error('Error triggering scheduled workflow:', error);
            this.showToast('Failed to trigger scheduled workflow', 'error');
            this.updateWorkflowStatus('Error');
        } finally {
            scheduledBtn.innerHTML = originalText;
            scheduledBtn.disabled = false;
        }
    }

    addLog(message, type = 'info') {
        const logsContainer = document.getElementById('logsContainer');
        if (!logsContainer) return;

        const timestamp = new Date().toLocaleTimeString();
        const logEntry = document.createElement('div');
        logEntry.className = `log-entry ${type}`;
        
        logEntry.innerHTML = `
            <span class="timestamp">[${timestamp}]</span> ${message}
        `;

        logsContainer.appendChild(logEntry);
        
        // Auto-scroll to bottom
        logsContainer.scrollTop = logsContainer.scrollHeight;
        
        // Limit log entries to 100
        while (logsContainer.children.length > 100) {
            logsContainer.removeChild(logsContainer.firstChild);
        }
    }

    showToast(message, type = 'info') {
        const toastContainer = document.getElementById('toastContainer');
        if (!toastContainer) return;
        
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.innerHTML = `
            <i class="fas fa-${type === 'success' ? 'check-circle' : type === 'error' ? 'exclamation-circle' : 'info-circle'}"></i>
            <span>${message}</span>
        `;
        
        toastContainer.appendChild(toast);
        
        // Auto remove after 5 seconds
        setTimeout(() => {
            toast.style.animation = 'slideOut 0.3s ease';
            setTimeout(() => toast.remove(), 300);
        }, 5000);
    }

    handlePartialCompletion(details) {
        this.updatePipelineStep(4, 'warning', 'Partially completed');
        this.addStepDetail(4, '⚠️ Some workflows failed to trigger', 'warning');
        this.updateWorkflowStatus('Partially Completed');
        this.showToast('Workflow partially completed. Check logs for details.', 'warning');
    }

    handleWorkflowStatus(message, details) {
        if (message.includes('WORKFLOW_SUCCESS')) {
            // Check if any agreements were actually processed
            const processedCount = details.agreements_processed_successfully || 0;
            const foundCount = details.agreements_found || 0;
            
            if (processedCount === 0 && foundCount > 0) {
                // All agreements were already processed
                this.updatePipelineStep(4, 'skipped', 'No workflows needed');
                this.updateWorkflowStatus('Completed - All Already Processed');
                this.showToast('All agreements were already processed. No workflows triggered.', 'info');
            } else if (processedCount > 0) {
                // Some agreements were processed
                this.updatePipelineStep(4, 'completed', `Triggered ${processedCount} workflow${processedCount > 1 ? 's' : ''} successfully`);
                this.updateWorkflowStatus('Completed Successfully');
                this.showToast(`Successfully triggered workflows for ${processedCount} agreement${processedCount > 1 ? 's' : ''}!`, 'success');
            } else {
                // No agreements found at all
                this.updatePipelineStep(4, 'skipped', 'No agreements to process');
                this.updateWorkflowStatus('Completed - Nothing to Process');
                this.showToast('No agreements found to process.', 'info');
            }
            // Re-enable input and button after completion
            this.enableWorkflowControls();
        } else if (message.includes('WORKFLOW_PARTIAL')) {
            this.updatePipelineStep(4, 'warning', 'Partially completed - some workflows failed');
            this.updateWorkflowStatus('Partially Completed');
            this.showToast('Workflow partially completed. Some agreements had errors.', 'warning');
            // Re-enable input and button after completion
            this.enableWorkflowControls();
        } else if (message.includes('WORKFLOW_FAILED')) {
            this.updatePipelineStep(4, 'error', 'Workflow failed');
            this.updateWorkflowStatus('Failed');
            this.showToast('Workflow failed. Please check the logs.', 'error');
            // Re-enable input and button after completion
            this.enableWorkflowControls();
        }
    }

    enableWorkflowControls() {
        const userIdInput = document.getElementById('userId');
        const triggerBtn = document.getElementById('triggerBtn');
        
        if (userIdInput) {
            userIdInput.disabled = false;
        }
        
        if (triggerBtn) {
            triggerBtn.disabled = false;
            triggerBtn.innerHTML = '<i class="fas fa-rocket"></i> Start Manual Workflow';
        }
    }


}

// Initialize dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    console.log('🚀 Price Adjustment Agent Dashboard starting...');
    window.dashboard = new PriceAdjustmentDashboard();
}); 