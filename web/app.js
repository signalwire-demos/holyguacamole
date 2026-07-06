// Holy Guacamole! Drive-Thru Order System
// Frontend handles display only - ALL pricing comes from backend

// Connection state - fetched from /get_token endpoint
let currentToken = null;
let currentDestination = null;

let client;
let call;
let isMuted = false;

// v4 SDK delivers everything as RxJS observables - track subscriptions so
// disconnect() can drop them all
let callSubscriptions = [];

// The <video> element we own for the remote (avatar) stream
let remoteVideo = null;
let lastTrackSignature = '';

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

// Fetch a guest token (+ destination address) from the backend.
// Throws with a useful message on any error shape, including the legacy
// Flask-style tuple (an array) that some deployed versions return with 200.
async function fetchGuestToken() {
    const resp = await fetch('/get_token');
    let data = await resp.json();
    if (Array.isArray(data)) {
        data = data[0] || {};
    }
    if (!resp.ok || data.error) {
        throw new Error(data.error || `Token request failed (HTTP ${resp.status})`);
    }
    if (!data.token || !data.address) {
        throw new Error('Token response missing token/address');
    }
    return data;
}

// Connect to SignalWire
async function connect() {
    try {
        connectBtn.disabled = true;
        connectBtn.textContent = 'Connecting...';
        updateStatus('greeting', 'Getting token...');

        // The v4 CDN bundle exposes the SignalWire class on the SignalWire namespace
        if (!window.SignalWire || typeof window.SignalWire.SignalWire !== 'function') {
            console.error('SignalWire SDK structure:', window.SignalWire);
            throw new Error('SignalWire.SignalWire constructor not found');
        }

        // Fetch the first token up front so a backend problem fails fast
        // (and gives us the destination address to dial)
        const tokenData = await fetchGuestToken();
        currentToken = tokenData.token;
        currentDestination = tokenData.address;
        console.log('Token received, destination:', currentDestination);

        // v4 client takes a credential provider; the SDK calls authenticate()
        // whenever it needs a (fresh) token. Guest tokens from /get_token work
        // as bearer credentials. Construction begins connecting immediately.
        console.log('Initializing SignalWire client...');
        let usedInitialToken = false;
        client = new window.SignalWire.SignalWire({
            authenticate: async () => {
                if (!usedInitialToken) {
                    usedInitialToken = true;
                    return { token: currentToken };
                }
                // SDK wants a fresh token (expiry/reconnect) - mint another
                const data = await fetchGuestToken();
                currentToken = data.token;
                return { token: data.token };
            }
        });

        // Surface SDK errors/warnings (replaces the old logLevel: 'debug')
        callSubscriptions.push(client.errors$.subscribe((err) => {
            console.error('SignalWire client error:', err);
            logEvent(`Client error: ${err?.message || err?.code || 'unknown'}`);
        }));
        callSubscriptions.push(client.warnings$.subscribe((warn) => {
            console.warn('SignalWire client warning:', warn?.code, warn?.message);
        }));

        updateStatus('greeting', 'Connecting to Sigmond...');

        // Wait until the client session is up before dialing.
        // isConnected$ replays its current value synchronously on subscribe,
        // so resolve via a flag and defer the unsubscribe. Time out rather
        // than hang if the connection never comes up.
        await new Promise((resolve, reject) => {
            let settled = false;
            const timer = setTimeout(() => {
                if (!settled) {
                    settled = true;
                    reject(new Error('Timed out connecting to SignalWire'));
                }
            }, 15000);
            const sub = client.isConnected$.subscribe({
                next: (connected) => {
                    if (connected && !settled) {
                        settled = true;
                        clearTimeout(timer);
                        setTimeout(() => sub.unsubscribe(), 0);
                        resolve();
                    }
                },
                error: (err) => {
                    if (!settled) {
                        settled = true;
                        clearTimeout(timer);
                        reject(err);
                    }
                }
            });
        });

        console.log('Client connected, dialing', currentDestination);

        // Dial the agent address. Video is receive-only: the avatar comes
        // from the platform, the customer only sends audio (v4 replacement
        // for negotiateVideo). dial() connects - there is no start() step.
        call = await client.dial(currentDestination, {
            audio: {
                echoCancellation: true,
                noiseSuppression: false,
                autoGainControl: false
            },
            video: false,
            receiveAudio: true,
            receiveVideo: true,
            userVariables: {
                userName: 'Holy Guacamole Customer',
                interface: 'web-ui',
                timestamp: new Date().toISOString(),
                extension: 'holy_guacamole'
            }
        });

        console.log('Call created:', call);

        // Render the remote (avatar) stream in our own video element
        setupRemoteMedia(call);

        // User events from the AI drive the order UI. Payload arrives in
        // evt.params; handleUserEvent unwraps both emit shapes.
        callSubscriptions.push(call.subscribe('user_event').subscribe((evt) => {
            console.log('🌶️ user_event', evt);
            handleUserEvent(evt?.params ?? evt);
        }));

        // Call lifecycle - one status$ stream replaces the old event zoo.
        // Deduplicate teardown: status can emit multiple terminal values and
        // the SDK completes subjects on destroy (sometimes without a
        // terminal status first).
        let sawConnected = false;
        let disconnectTriggered = false;

        const handleDisconnectEvent = (reason) => {
            console.log(`Call ended (${reason})`);
            if (disconnectTriggered) {
                return;
            }
            disconnectTriggered = true;
            updateStatus('greeting', 'Call ended. Thank you for visiting Holy Guacamole!');
            // Use setTimeout to ensure we don't interrupt the event flow
            setTimeout(() => disconnect(), 100);
        };

        callSubscriptions.push(call.status$.subscribe({
            next: (status) => {
                console.log('Call status:', status);
                if (status === 'connected' && !sawConnected) {
                    sawConnected = true;
                    onCallConnected();
                } else if (status === 'disconnected' || status === 'failed' || status === 'destroyed') {
                    handleDisconnectEvent(status);
                }
            },
            complete: () => handleDisconnectEvent('complete'),
            error: (err) => {
                console.error('Call status error:', err);
                handleDisconnectEvent('error');
            }
        }));

    } catch (error) {
        console.error('Connection error:', error);
        disconnect();
        updateStatus('greeting', `Connection failed: ${error.message || 'Please try again.'}`);
    }
}

// UI changes when the call reaches 'connected'
function onCallConnected() {
    connectBtn.style.display = 'none';
    hangupBtn.style.display = 'inline-block';
    muteBtn.style.display = 'inline-block';

    updateStatus('greeting', 'Connected! Ready to take your order.');

    // Hide the video placeholder
    const placeholder = document.getElementById('video-placeholder');
    if (placeholder) {
        placeholder.style.display = 'none';
    }

    // Honor the "start muted" checkbox
    if (startMutedCheckbox.checked && !isMuted) {
        toggleMute();
    }

    logEvent('Connected to Sigmond');
}

// Attach the remote stream (avatar video + audio) to a <video> element we
// own inside #video-container. v4 has no rootElement - media is rendered by
// the app. The SDK re-emits the same MediaStream object as tracks arrive,
// so re-attach whenever the track set changes, not just on a new stream.
function setupRemoteMedia(activeCall) {
    const videoContainer = document.getElementById('video-container');

    remoteVideo = document.createElement('video');
    remoteVideo.id = 'remote-video';
    remoteVideo.autoplay = true;
    remoteVideo.playsInline = true;
    remoteVideo.setAttribute('playsinline', '');
    videoContainer.appendChild(remoteVideo);

    lastTrackSignature = '';
    callSubscriptions.push(activeCall.remoteStream$.subscribe((stream) => {
        if (!stream || !remoteVideo) {
            return;
        }
        const signature = stream.getTracks().map((t) => `${t.kind}:${t.id}`).sort().join('|');
        if (remoteVideo.srcObject === stream && signature === lastTrackSignature) {
            return;
        }
        lastTrackSignature = signature;
        console.log('Attaching remote stream, tracks:', signature);
        remoteVideo.srcObject = stream;
        remoteVideo.play().catch((e) => console.warn('Video play() blocked:', e));
    }));
}

// Disconnect and cleanup
function disconnect() {
    console.log('Disconnect called - cleaning up...');

    // Drop all RxJS subscriptions from this session
    callSubscriptions.forEach((sub) => {
        try {
            sub.unsubscribe();
        } catch (e) {
            // already closed
        }
    });
    callSubscriptions = [];

    // Clean up the call reference
    call = null;
    remoteVideo = null;
    lastTrackSignature = '';

    // Disconnect the client properly (closes the socket and releases media)
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
async function toggleMute() {
    if (!call) return;

    const wantMuted = !isMuted;

    // v4 exposes mute on the self participant; it falls back to a local
    // device mute if the server RPC fails
    try {
        if (wantMuted) {
            await call.self.mute();
        } else {
            await call.self.unmute();
        }
        isMuted = wantMuted;
    } catch (e) {
        console.error('Error toggling mute:', e);
        return;
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
        if (call) {
            console.log('Hanging up call...');
            await call.hangup();
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
    if (call && e.target.checked) {
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
    if (call) {
        hangup();
    }
});
