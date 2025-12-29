// Holy Guacamole! Drive-Thru Order System
// Frontend handles display only - ALL pricing comes from backend

// Configuration
const DESTINATION = '/public/holy-guacamole';
const STATIC_TOKEN = 'eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIiwidHlwIjoiU0FUIiwiY2giOiJwdWMuc2lnbmFsd2lyZS5jb20ifQ..D_TMjgEuu4yuF6fX.t47i-K3zYMSIU3IdFOXmC_8JtIVlDXkanuID9kEq1uM_cmJwnNtEHIG-LCYLMB5l2MfL5veedK2cejbD_o_dj-pSZgYqh4Qn9e7Lmv1BQBnqwCLsc7nZ1U3kg0c6TYZvFWOR3rgrUAlBcP4G_VSl7IjgRorh2dL4BoCTsMSvuLoKyBzvRs2JbZGmJ9UjI0JYzXdlVf1h24Pa3MP0SoPwyQ9Z7wGmxIXdHJ_ovdVspkCO60oFlmIMhn_WbW2jGq-n8TkclEkM_cLcz1OjUDhai_xMYeqBJlfJNz2OoD8UIg1e60d0fc2NP84wU1_uYgZ4CCe9lIuVmzpsQkun-pfMDMgUcR9Uf4YpVEZDToecvP7tzgSgwvTNR3o9FBEHhDienhuYiHE35uWs9ktoElMxYR28BdF7H3YwFuzg2TvHtzUhi80IORVPLPWtH4goagDFbel_zEWc_nn8xcahpbrC4sKlTIJ1I9iPfDsCOAps30uVlHFRq7LElGQw_15QqU2S13GDneBv9uAQGRVxnKD5h_moogKfuofRp3EcCxTe.65H02il38BhCkGPkDVM8xA';

let client;
let roomSession;
let isMuted = false;

// Order state - display only, truth comes from backend
let orderDisplay = {
    items: [],
    subtotal: 0,
    tax: 0,
    total: 0,
    orderNumber: null,
    status: 'greeting'
};

// Fetch and display menu from backend
async function loadMenu() {
    try {
        const response = await fetch('/api/menu');
        const data = await response.json();
        displayMenu(data.menu);
    } catch (error) {
        console.error('Failed to load menu:', error);
    }
}

// Display menu in two-column layout
function displayMenu(menu) {
    const menuContainer = document.getElementById('menu-display');
    if (!menuContainer) {
        console.error('Menu display container not found');
        return;
    }
    
    menuContainer.innerHTML = '';
    
    // Define category order and icons
    const categoryOrder = ['tacos', 'burritos', 'quesadillas', 'sides', 'drinks', 'combos'];
    const categoryIcons = {
        tacos: '🌮',
        burritos: '🌯',
        quesadillas: '🧀',
        sides: '🍟',
        drinks: '🥤',
        combos: '💰'
    };
    
    // Display each category in the two-column grid
    categoryOrder.forEach(category => {
        if (!menu[category]) return;
        
        const categoryDiv = document.createElement('div');
        categoryDiv.className = 'menu-category';
        
        // Category header
        const header = document.createElement('div');
        header.className = 'menu-category-title';
        header.textContent = `${categoryIcons[category] || ''} ${category.charAt(0).toUpperCase() + category.slice(1)}`;
        categoryDiv.appendChild(header);
        
        // Menu items section
        const itemsSection = document.createElement('div');
        itemsSection.className = 'menu-section';
        
        // Add items
        Object.entries(menu[category]).forEach(([sku, item]) => {
            const itemDiv = document.createElement('div');
            itemDiv.className = 'menu-item';
            
            const leftDiv = document.createElement('div');
            leftDiv.style.flex = '1';
            
            const nameSpan = document.createElement('div');
            nameSpan.className = 'menu-item-name';
            nameSpan.textContent = item.name;
            
            const descSpan = document.createElement('div');
            descSpan.className = 'menu-item-desc';
            descSpan.textContent = item.description || '';
            
            leftDiv.appendChild(nameSpan);
            if (item.description) {
                leftDiv.appendChild(descSpan);
            }
            
            const priceSpan = document.createElement('span');
            priceSpan.className = 'menu-item-price';
            priceSpan.textContent = `$${item.price.toFixed(2)}`;
            
            itemDiv.appendChild(leftDiv);
            itemDiv.appendChild(priceSpan);
            itemsSection.appendChild(itemDiv);
        });
        
        categoryDiv.appendChild(itemsSection);
        menuContainer.appendChild(categoryDiv);
    });
}

// UI Elements
const connectBtn = document.getElementById('connectBtn');
const hangupBtn = document.getElementById('hangupBtn');
const muteBtn = document.getElementById('muteBtn');
const startMutedCheckbox = document.getElementById('startMuted');
const showLogCheckbox = document.getElementById('showLog');
const statusDiv = document.getElementById('status');
const eventLogContainer = document.getElementById('event-log-container');
const eventEntries = document.getElementById('event-entries');
const orderItems = document.getElementById('order-items');
const orderTotals = document.getElementById('order-totals');
const orderNumberDiv = document.getElementById('order-number');
const orderNumberValue = document.getElementById('order-number-value');
const resultMessage = document.getElementById('result-message');


// Event logging
function logEvent(message, data = null, isUserEvent = false) {
    const entry = document.createElement('div');
    entry.className = isUserEvent ? 'event-entry user-event' : 'event-entry';
    const time = new Date().toLocaleTimeString();
    
    let dataStr = '';
    if (data) {
        try {
            dataStr = JSON.stringify(data, null, 2);
        } catch (e) {
            dataStr = 'Error serializing data';
        }
    }
    
    entry.innerHTML = `
        <div style="color: #666; font-size: 0.8rem;">${time}</div>
        <div>${isUserEvent ? '🌮 ' : ''}${message}</div>
        ${dataStr ? `<pre style="color: #888; margin-left: 10px; font-size: 0.8rem;">${dataStr}</pre>` : ''}
    `;
    eventEntries.appendChild(entry);
    // Use requestAnimationFrame to ensure DOM has updated before scrolling
    requestAnimationFrame(() => {
        eventEntries.scrollTop = eventEntries.scrollHeight;
    });
}

// Update status display
function updateStatus(status, message) {
    statusDiv.className = '';
    
    switch(status) {
        case 'greeting':
            statusDiv.className = 'status-greeting';
            statusDiv.textContent = message || 'Welcome to Holy Guacamole!';
            break;
        case 'ordering':
            statusDiv.className = 'status-ordering';
            statusDiv.textContent = message || 'Taking your order...';
            break;
        case 'confirming':
        case 'confirming_order':
            statusDiv.className = 'status-confirming';
            statusDiv.textContent = message || 'Confirming your order...';
            break;
        case 'payment':
        case 'payment_processing':
            statusDiv.className = 'status-payment';
            statusDiv.textContent = message || 'Processing payment...';
            break;
        case 'order_complete':
            statusDiv.className = 'status-payment';
            statusDiv.textContent = message || 'Order Complete!';
            break;
        default:
            statusDiv.textContent = message || 'Ready';
    }
    
    orderDisplay.status = status;
}

// Update order display - ALL values from backend
function updateOrderDisplay() {
    if (orderDisplay.items.length === 0) {
        orderItems.innerHTML = `
            <div style="text-align: center; color: #999; padding: 50px;">
                Your order will appear here
            </div>
        `;
        orderTotals.style.display = 'none';
        return;
    }
    
    // Display order items with descriptions
    orderItems.innerHTML = '';
    orderDisplay.items.forEach(item => {
        const itemDiv = document.createElement('div');
        itemDiv.style.marginBottom = '12px';
        itemDiv.innerHTML = `
            <div class="order-item">
                <span class="order-item-name">${item.quantity}x ${item.name}</span>
                <span class="order-item-price">$${item.total.toFixed(2)}</span>
            </div>
            ${item.description ? `<div style="font-size: 0.85rem; color: #666; padding: 0 10px; margin-top: -5px;">${item.description}</div>` : ''}
        `;
        orderItems.appendChild(itemDiv);
    });
    
    // Update totals from backend
    document.getElementById('subtotal').textContent = `$${orderDisplay.subtotal.toFixed(2)}`;
    document.getElementById('tax').textContent = `$${orderDisplay.tax.toFixed(2)}`;
    document.getElementById('total').textContent = `$${orderDisplay.total.toFixed(2)}`;
    orderTotals.style.display = 'block';
}

// Show temporary result message
function showResult(message, duration = 3000) {
    resultMessage.textContent = message;
    resultMessage.classList.add('show');
    
    setTimeout(() => {
        resultMessage.classList.remove('show');
    }, duration);
}

// Handle user events from backend
function handleUserEvent(params) {
    console.log('Handling user event:', params);
    
    let eventData = params;
    if (params && params.event) {
        eventData = params.event;
    }
    
    if (!eventData || !eventData.type) {
        console.log('No valid event data found');
        return;
    }
    
    switch(eventData.type) {
        case 'order_started':
            orderDisplay.items = [];
            orderDisplay.subtotal = 0;
            orderDisplay.tax = 0;
            orderDisplay.total = 0;
            orderDisplay.orderNumber = null;
            updateOrderDisplay();
            updateStatus('ordering', 'What would you like today?');
            logEvent('New order started', eventData, true);
            break;
            
        case 'item_added':
            // Add or update item in display - using backend values
            const existingItem = orderDisplay.items.find(i => i.sku === eventData.item.sku);
            if (existingItem) {
                console.log(`[DEBUG] Updating existing ${existingItem.name}: ${existingItem.quantity} -> ${eventData.item.quantity}`);
                existingItem.quantity = eventData.item.quantity;
                existingItem.total = eventData.item.total;
            } else {
                console.log(`[DEBUG] Adding new item: ${eventData.item.name} x${eventData.item.quantity}`);
                orderDisplay.items.push({
                    sku: eventData.item.sku,
                    name: eventData.item.name,
                    description: eventData.item.description || '',
                    quantity: eventData.item.quantity,
                    price: eventData.item.price,
                    total: eventData.item.total
                });
            }
            
            // Update totals from backend - ALL values come from backend
            orderDisplay.total = eventData.order_total || 0;
            orderDisplay.subtotal = eventData.subtotal || 0;
            orderDisplay.tax = eventData.tax || 0;
            updateOrderDisplay();
            updateStatus('ordering', `Added ${eventData.item.name}`);
            showResult(`Updated: ${eventData.item.quantity}x ${eventData.item.name}`);
            logEvent('Item added', eventData, true);
            break;
            
        case 'item_removed':
            // Remove item from display
            orderDisplay.items = orderDisplay.items.filter(i => i.sku !== eventData.sku);
            orderDisplay.total = eventData.order_total;
            orderDisplay.subtotal = eventData.subtotal || 0;
            orderDisplay.tax = eventData.tax || 0;
            updateOrderDisplay();
            updateStatus('ordering', 'Item removed');
            showResult('Item removed from order');
            logEvent('Item removed', eventData, true);
            break;
            
        case 'quantity_modified':
            // Update quantity using backend values
            const modItem = orderDisplay.items.find(i => i.sku === eventData.sku);
            if (modItem) {
                if (eventData.new_quantity === 0) {
                    orderDisplay.items = orderDisplay.items.filter(i => i.sku !== eventData.sku);
                } else {
                    modItem.quantity = eventData.new_quantity;
                    modItem.total = eventData.new_total;  // Fixed: was item_total, should be new_total
                }
            }
            orderDisplay.total = eventData.order_total;
            orderDisplay.subtotal = eventData.subtotal || 0;
            orderDisplay.tax = eventData.tax || 0;
            updateOrderDisplay();
            updateStatus('ordering', `Quantity updated`);
            showResult(`Updated quantity to ${eventData.new_quantity}`);
            logEvent('Quantity modified', eventData, true);
            break;
            
        case 'order_reviewing':
            // Display complete order with backend calculations
            orderDisplay.items = eventData.items;
            orderDisplay.subtotal = eventData.subtotal;
            orderDisplay.tax = eventData.tax;
            orderDisplay.total = eventData.total;
            updateOrderDisplay();
            updateStatus('confirming', 'Please confirm your order');
            logEvent('Order review', eventData, true);
            break;
            
        case 'order_confirmed':
            // Show order number and total from backend
            orderDisplay.orderNumber = eventData.order_number;
            orderDisplay.total = eventData.total;
            orderNumberValue.textContent = eventData.order_number;
            orderNumberDiv.style.display = 'block';
            updateStatus('payment', `Order #${eventData.order_number} confirmed!`);
            showResult(`Order #${eventData.order_number} - Total: $${eventData.total.toFixed(2)}`, 5000);
            logEvent('Order confirmed', eventData, true);
            break;
            
        case 'payment_ready':
            // Direct to payment window
            updateStatus('payment', 'Please pull forward to the first window');
            showResult(`Pull forward to the first window for payment`, 5000);
            logEvent('Payment ready', eventData, true);
            break;
            
        case 'suggestion_made':
            // Show combo suggestion
            showResult(eventData.message, 5000);
            logEvent('Combo suggestion', eventData, true);
            break;
            
        case 'order_cancelled':
            // Reset everything
            orderDisplay.items = [];
            orderDisplay.subtotal = 0;
            orderDisplay.tax = 0;
            orderDisplay.total = 0;
            orderDisplay.orderNumber = null;
            orderNumberDiv.style.display = 'none';
            updateOrderDisplay();
            updateStatus('greeting', 'Order cancelled');
            logEvent('Order cancelled', eventData, true);
            break;
            
        case 'show_menu':
            // Could highlight menu items if needed
            logEvent('Menu shown', eventData, true);
            break;
            
        case 'order_finalized':
            // Sync the complete order from backend
            if (eventData.items) {
                orderDisplay.items = eventData.items;
                orderDisplay.subtotal = eventData.subtotal;
                orderDisplay.tax = eventData.tax;
                orderDisplay.total = eventData.total;
                updateOrderDisplay();
            }
            updateStatus('confirming_order', 'Please confirm your order');
            logEvent('Order finalized', eventData, true);
            break;
            
        case 'payment_started':
            orderDisplay.orderNumber = eventData.order_number;
            orderDisplay.total = eventData.total;
            updateOrderDisplay();
            updateStatus('payment_processing', `Order #${eventData.order_number} - Please pull forward`);
            showResult(`Order #${eventData.order_number} - Total: $${eventData.total.toFixed(2)}`, 5000);
            logEvent('Payment started', eventData, true);
            break;
            
        case 'order_completed':
            updateStatus('order_complete', `Order #${eventData.order_number} complete!`);
            // Clear the order display but show order number
            orderDisplay.items = [];
            orderDisplay.subtotal = 0;
            orderDisplay.tax = 0;
            orderDisplay.total = 0;
            orderDisplay.orderNumber = eventData.order_number;
            updateOrderDisplay();
            // Show order complete message
            document.getElementById('order-items').innerHTML = `
                <div style="text-align: center; padding: 20px;">
                    <h2 style="color: #4CAF50;">✅ Order Complete!</h2>
                    <p style="font-size: 24px; font-weight: bold;">Order #${eventData.order_number}</p>
                </div>
            `;
            showResult(`Order #${eventData.order_number} complete! Thank you!`);
            logEvent('Order completed', eventData, true);
            break;
            
        case 'new_order':
            // Reset for new order
            orderDisplay.items = [];
            orderDisplay.subtotal = 0;
            orderDisplay.tax = 0;
            orderDisplay.total = 0;
            orderDisplay.orderNumber = null;
            updateOrderDisplay();
            updateStatus('greeting', 'Ready for new order');
            logEvent('New order started', eventData, true);
            break;
            
        case 'combo_upgraded':
            // Replace entire order with upgraded version
            console.log('Combo upgrade:', eventData);
            
            // Update order with new items array
            orderDisplay.items = eventData.items;
            orderDisplay.subtotal = eventData.subtotal;
            orderDisplay.tax = eventData.tax;
            orderDisplay.total = eventData.total;
            
            // Update display
            updateOrderDisplay();
            
            // Show what was replaced
            let upgradeMessage = '';
            if (eventData.added_combos) {
                // Multiple combos upgraded
                const comboNames = eventData.added_combos.map(c => c.name).join(' and ');
                upgradeMessage = `Upgraded to ${comboNames}!`;
            } else if (eventData.added_combo) {
                // Single combo upgraded
                upgradeMessage = `Upgraded to ${eventData.added_combo.name}!`;
            }
            if (eventData.savings > 0) {
                upgradeMessage += ` Saved $${eventData.savings.toFixed(2)}!`;
            }
            
            // Show removed items
            if (eventData.removed_items && eventData.removed_items.length > 0) {
                const removedNames = eventData.removed_items.map(item => 
                    item.quantity > 1 ? `${item.quantity}x ${item.name}` : item.name
                ).join(', ');
                console.log(`Replaced: ${removedNames}`);
            }
            
            updateStatus('ordering', upgradeMessage);
            showResult(upgradeMessage, 3000);
            logEvent('Combo upgraded', eventData, true);
            break;
            
        case 'order_reviewed':
            // Update display with full order details
            if (eventData.items) {
                orderDisplay.items = eventData.items;
                orderDisplay.subtotal = eventData.subtotal;
                orderDisplay.tax = eventData.tax;
                orderDisplay.total = eventData.total;
                updateOrderDisplay();
            }
            logEvent('Order reviewed', eventData, true);
            break;
            
        case 'menu_displayed':
            logEvent('Menu displayed', eventData, true);
            break;
    }
}

// Connect to SignalWire
async function connect() {
    try {
        connectBtn.disabled = true;
        connectBtn.textContent = 'Connecting...';
        updateStatus('greeting', 'Connecting to Sigmond...');
        
        // Check if token is valid
        if (!STATIC_TOKEN || STATIC_TOKEN === 'YOUR_TOKEN_HERE') {
            throw new Error('Please update STATIC_TOKEN with your actual SignalWire token');
        }
        
        // Initialize SignalWire client from CDN
        // The CDN exports SignalWire.SignalWire as the main function
        if (window.SignalWire && typeof window.SignalWire.SignalWire === 'function') {
            console.log('Initializing SignalWire client...');
            client = await window.SignalWire.SignalWire({
                token: STATIC_TOKEN,
                logLevel: 'debug'
            });
        } else {
            console.error('SignalWire SDK structure:', window.SignalWire);
            throw new Error('SignalWire.SignalWire function not found');
        }
        
        console.log('Client initialized successfully');
        
        // Subscribe to user events at client level
        client.on('user_event', (params) => {
            console.log('🌮 CLIENT EVENT: user_event', params);
            handleUserEvent(params);
        });
        
        // Get video container for remote video
        const videoContainer = document.getElementById('video-container');
        
        // Dial the call with video negotiation
        roomSession = await client.dial({
            to: DESTINATION,
            rootElement: videoContainer,
            audio: {
                echoCancellation: true,
                noiseSuppression: false,
                autoGainControl: false
            },
            video: true,
            negotiateVideo: true,  // Important for video negotiation
            userVariables: {
                userName: 'Holy Guacamole Customer',
                interface: 'web-ui',
                timestamp: new Date().toISOString(),
                extension: 'holy_guacamole'
            }
        });
        
        console.log('Room session created:', roomSession);
        
        // Subscribe to room session events
        roomSession.on('call.joined', async (params) => {
            console.log('Call joined:', params);
            connectBtn.style.display = 'none';
            hangupBtn.style.display = 'inline-block';
            muteBtn.style.display = 'inline-block';
            
            updateStatus('greeting', 'Connected! Ready to take your order.');
            
            // Hide the video placeholder
            const placeholder = document.getElementById('video-placeholder');
            if (placeholder) {
                placeholder.style.display = 'none';
            }
            
            // Log local stream info if available
            if (roomSession.localStream) {
                console.log('Local stream available:', roomSession.localStream);
                const videoTracks = roomSession.localStream.getVideoTracks();
                const audioTracks = roomSession.localStream.getAudioTracks();
                console.log(`Local stream: ${videoTracks.length} video tracks, ${audioTracks.length} audio tracks`);
            }
            
            // Check video elements after a short delay
            setTimeout(() => {
                const videoContainer = document.getElementById('video-container');
                const videos = videoContainer.querySelectorAll('video');
                console.log('Video elements in container:', videos.length);
                videos.forEach((video, index) => {
                    console.log(`Video ${index}:`, video, 'Has stream:', !!video.srcObject);
                });
            }, 1000);
            
            logEvent('Connected to Sigmond');
        });
        
        roomSession.on('call.state', (params) => {
            console.log('Got call.state event:', params);
            
            // Try different paths to find the call_state
            const callState = params?.payload?.call_state || 
                             params?.call_state || 
                             params?.state;
            
            console.log('Extracted call state:', callState);
            
            if (callState === 'ending' || callState === 'ended' || callState === 'hangup') {
                console.log('Call ending - triggering disconnect');
                updateStatus('greeting', 'Call ended. Thank you for visiting Holy Guacamole!');
                // Use setTimeout to ensure we don't interrupt the event flow
                setTimeout(() => disconnect(), 100);
            }
        });
        
        // Single handler for disconnection events - avoid duplicates
        let disconnectTriggered = false;
        
        const handleDisconnectEvent = (eventName, params) => {
            console.log(`${eventName} event:`, params);
            
            // Prevent multiple disconnect calls
            if (disconnectTriggered) {
                console.log('Disconnect already triggered, ignoring');
                return;
            }
            
            disconnectTriggered = true;
            updateStatus('greeting', 'Call ended. Thank you!');
            
            // Reset flag after disconnect completes
            setTimeout(() => {
                disconnect();
                disconnectTriggered = false;
            }, 100);
        };
        
        // Listen for various disconnect events
        roomSession.on('destroy', (params) => handleDisconnectEvent('destroy', params));
        roomSession.on('disconnected', (params) => handleDisconnectEvent('disconnected', params));
        roomSession.on('room.left', (params) => handleDisconnectEvent('room.left', params));
        roomSession.on('call.ended', (params) => handleDisconnectEvent('call.ended', params));
        
        // Handle member left - but only if it's the remote party
        roomSession.on('member.left', (params) => {
            console.log('Member left:', params);
            // Only disconnect if it's not us who left
            if (params.member?.id !== roomSession.memberId) {
                handleDisconnectEvent('member.left (remote)', params);
            }
        });
        
        // Handle user events on room session too
        roomSession.on('user_event', (params) => {
            console.log('🌶️ ROOM EVENT: user_event', params);
            handleUserEvent(params);
        });
        
        // Additional events for debugging
        roomSession.on('room.started', (params) => {
            console.log('Room started:', params);
        });
        
        roomSession.on('stream.started', (params) => {
            console.log('Stream started:', params);
        });
        
        roomSession.on('stream.ended', (params) => {
            console.log('Stream ended:', params);
        });
        
        // START THE CALL - Critical!
        await roomSession.start();
        console.log('Call started successfully');
        
    } catch (error) {
        console.error('Connection error:', error);
        updateStatus('greeting', 'Connection failed. Please try again.');
        connectBtn.disabled = false;
        connectBtn.textContent = '🎤 Start Ordering';
    }
}

// Disconnect and cleanup
function disconnect() {
    console.log('Disconnect called - cleaning up...');
    
    // Clean up local stream if it exists
    if (roomSession && roomSession.localStream) {
        console.log('Stopping local stream tracks');
        roomSession.localStream.getTracks().forEach(track => {
            track.stop();
        });
    }
    
    // Clean up room session
    roomSession = null;
    
    // Disconnect the client properly
    if (client) {
        try {
            console.log('Disconnecting client');
            client.disconnect();
        } catch (e) {
            console.log('Client disconnect error:', e);
        }
        client = null;
    }
    
    // Clean up video container
    const videoContainer = document.getElementById('video-container');
    if (videoContainer) {
        console.log('Cleaning video container');
        
        // Stop any video streams in the container
        const videos = videoContainer.querySelectorAll('video');
        videos.forEach(video => {
            if (video.srcObject) {
                video.srcObject.getTracks().forEach(track => track.stop());
                video.srcObject = null;
            }
        });
        
        // Clear and restore placeholder
        videoContainer.innerHTML = '';
        const placeholder = document.createElement('div');
        placeholder.id = 'video-placeholder';
        placeholder.innerHTML = `
            <img src="/logo.png" alt="Holy Guacamole Logo" style="max-width: 200px; opacity: 0.7;">
            <p style="color: #568203; margin-top: 20px; font-size: 1.1rem;">Click "Start Ordering" to begin</p>
        `;
        videoContainer.appendChild(placeholder);
    }
    
    // Force UI reset - get fresh references to ensure we have the right elements
    const connectButton = document.getElementById('connectBtn');
    const hangupButton = document.getElementById('hangupBtn');
    const muteButton = document.getElementById('muteBtn');
    
    console.log('Resetting UI buttons', { connectButton, hangupButton, muteButton });
    
    if (connectButton) {
        connectButton.style.display = 'inline-block';
        connectButton.disabled = false;
        connectButton.textContent = '🎤 Start Ordering';
        console.log('Connect button reset');
    } else {
        console.error('connectBtn not found!');
    }
    
    if (hangupButton) {
        hangupButton.style.display = 'none';
        console.log('Hangup button hidden');
    } else {
        console.error('hangupBtn not found!');
    }
    
    if (muteButton) {
        muteButton.style.display = 'none';
        muteButton.textContent = '🔇 Mute';
        isMuted = false;
        console.log('Mute button hidden and reset');
    } else {
        console.error('muteBtn not found!');
    }
    
    // Reset order display immediately
    orderDisplay.items = [];
    orderDisplay.subtotal = 0;
    orderDisplay.tax = 0;
    orderDisplay.total = 0;
    orderDisplay.orderNumber = null;
    
    // Hide order number if it exists
    const orderNumDiv = document.getElementById('order-number');
    if (orderNumDiv) {
        orderNumDiv.style.display = 'none';
    }
    
    updateOrderDisplay();
    
    // Update status message
    updateStatus('greeting', 'Welcome to Holy Guacamole!');
}

// Toggle mute
function toggleMute() {
    if (!roomSession) return;
    
    isMuted = !isMuted;
    
    // For Call objects, we need to mute the local stream differently
    try {
        if (roomSession.localStream) {
            // Mute/unmute the audio track
            const audioTracks = roomSession.localStream.getAudioTracks();
            audioTracks.forEach(track => {
                track.enabled = !isMuted;
            });
        } else if (roomSession.peer && roomSession.peer.localStream) {
            // Try alternate method
            const audioTracks = roomSession.peer.localStream.getAudioTracks();
            audioTracks.forEach(track => {
                track.enabled = !isMuted;
            });
        } else {
            console.warn('Unable to find local stream to mute/unmute');
        }
    } catch (e) {
        console.error('Error toggling mute:', e);
    }
    
    muteBtn.textContent = isMuted ? '🔊 Unmute' : '🔇 Mute';
    
    if (isMuted) {
        showResult('Microphone muted');
    } else {
        showResult('Microphone unmuted');
    }
}

// Hangup function
async function hangup() {
    try {
        if (roomSession) {
            console.log('Hanging up call...');
            await roomSession.hangup();
            console.log('Call hung up successfully');
        }
    } catch (error) {
        console.error('Hangup error:', error);
        // Continue with disconnect even if hangup fails
    }
    
    // Always disconnect to clean up
    disconnect();
}

// Event listeners
connectBtn.addEventListener('click', connect);
hangupBtn.addEventListener('click', hangup);
muteBtn.addEventListener('click', toggleMute);

showLogCheckbox.addEventListener('change', (e) => {
    eventLogContainer.style.display = e.target.checked ? 'block' : 'none';
});

startMutedCheckbox.addEventListener('change', (e) => {
    if (roomSession && e.target.checked) {
        // Use the toggleMute function if we're connected
        if (!isMuted) {
            toggleMute();
        }
    }
});

// Initialize on load
document.addEventListener('DOMContentLoaded', () => {
    // Load menu from backend
    loadMenu();
    updateStatus('greeting', 'Welcome to Holy Guacamole!');
    logEvent('Application initialized');
});

// Handle page unload
window.addEventListener('beforeunload', () => {
    if (roomSession) {
        hangup();
    }
});
