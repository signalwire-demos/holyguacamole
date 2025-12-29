# Holy Guacamole User Events Documentation

This document describes all user events emitted by the Holy Guacamole AI agent to the frontend client via SignalWire's `swml_user_event` system. These events enable real-time synchronization between the backend order state and the frontend UI.

## Overview

User events are sent from the Python backend to the JavaScript frontend through the WebRTC connection. They are received by the client's event handler and used to update the UI in real-time as customers interact with the AI agent.

### Event Structure

All events follow this basic structure:
```javascript
{
    "type": "event_name",
    // Event-specific data fields
}
```

## Event Types

### 1. `item_added`

**Purpose:** Notifies when an item is added to the order

**Emitted by:** `add_item()` function

**Data Structure:**
```javascript
{
    "type": "item_added",
    "items": [
        {
            "sku": "T001",
            "name": "Beef Taco",
            "quantity": 2,
            "price": 3.49,
            "total": 6.98
        }
        // ... all items in order
    ],
    "item_added": {  // Details of the specific item added
        "name": "Beef Taco",
        "quantity": 2
    },
    "order_total": 15.67,
    "subtotal": 14.25,
    "tax": 1.42,
    "total": 15.67
}
```

**Frontend Action:** Updates order display, shows new item animation, updates totals

---

### 2. `item_removed`

**Purpose:** Notifies when an item is completely removed from the order

**Emitted by:** `remove_item()` function when quantity reaches 0

**Data Structure:**
```javascript
{
    "type": "item_removed",
    "sku": "T001",
    "order_total": 12.50,
    "subtotal": 11.36,
    "tax": 1.14,
    "total": 12.50,
    "items": [/* updated items array */]
}
```

**Frontend Action:** Removes item from display, updates totals

---

### 3. `quantity_modified`

**Purpose:** Notifies when an item's quantity is changed but not removed

**Emitted by:** `remove_item()` function when partial quantity removed

**Data Structure:**
```javascript
{
    "type": "quantity_modified",
    "sku": "T001",
    "new_quantity": 1,
    "new_total": 3.49,
    "order_total": 10.99,
    "subtotal": 9.99,
    "tax": 1.00,
    "total": 10.99,
    "items": [/* updated items array */]
}
```

**Frontend Action:** Updates item quantity display, recalculates totals

---

### 4. `quantity_changed`

**Purpose:** Notifies when quantity is directly modified

**Emitted by:** `modify_quantity()` function

**Data Structure:**
```javascript
{
    "type": "quantity_changed",
    "sku": "B001",
    "new_quantity": 3,
    "order_total": 30.56,
    "subtotal": 27.78,
    "tax": 2.78,
    "total": 30.56,
    "items": [/* updated items array */]
}
```

**Frontend Action:** Updates specific item quantity and totals

---

### 5. `order_reviewed`

**Purpose:** Sends complete order for review display

**Emitted by:** `review_order()` function

**Data Structure:**
```javascript
{
    "type": "order_reviewed",
    "items": [
        {
            "sku": "T001",
            "name": "Beef Taco",
            "quantity": 2,
            "price": 3.49,
            "total": 6.98
        }
        // ... all items
    ],
    "subtotal": 14.25,
    "tax": 1.42,
    "total": 15.67
}
```

**Frontend Action:** Can highlight or animate the order display for review

---

### 6. `order_finalized`

**Purpose:** Notifies that order is ready for confirmation

**Emitted by:** `finalize_order()` function

**Data Structure:**
```javascript
{
    "type": "order_finalized",
    "items": [/* complete items array */],
    "subtotal": 14.25,
    "tax": 1.42,
    "total": 15.67,
    "item_count": 3
}
```

**Frontend Action:** Updates status to "confirming", may disable add/remove buttons

---

### 7. `payment_started`

**Purpose:** Notifies that payment processing has begun

**Emitted by:** `process_payment()` function

**Data Structure:**
```javascript
{
    "type": "payment_started",
    "order_number": 1042,
    "total": 15.67
}
```

**Frontend Action:** Shows payment processing UI, displays order number

---

### 8. `order_completed`

**Purpose:** Notifies that order is complete

**Emitted by:** `complete_order()` function

**Data Structure:**
```javascript
{
    "type": "order_completed",
    "order_number": 1042
}
```

**Frontend Action:** Shows completion message, displays order number prominently

---

### 9. `order_cancelled`

**Purpose:** Notifies that order has been cancelled/cleared

**Emitted by:** `cancel_order()` function

**Data Structure:**
```javascript
{
    "type": "order_cancelled",
    "items": [],  // Always empty array
    "subtotal": 0,
    "tax": 0,
    "total": 0
}
```

**Frontend Action:** Clears entire order display, resets to initial state

---

### 10. `new_order`

**Purpose:** Notifies that a new order is starting

**Emitted by:** `new_order()` function

**Data Structure:**
```javascript
{
    "type": "new_order"
}
```

**Frontend Action:** Resets UI for new order, clears previous order number

---

### 11. `combo_upgraded`

**Purpose:** Notifies when items are upgraded to combo meal(s)

**Emitted by:** `upgrade_to_combo()` function

**Data Structure (Single Combo):**
```javascript
{
    "type": "combo_upgraded",
    "items": [/* updated items array with combo */],
    "removed_items": [
        {"name": "Beef Taco", "quantity": 2},
        {"name": "Chips and Salsa", "quantity": 1},
        {"name": "Small Fountain Drink", "quantity": 1}
    ],
    "added_combo": {
        "name": "Taco Combo",
        "price": 9.99,
        "description": "2 tacos, chips & salsa, and a drink"
    },
    "subtotal": 9.99,
    "tax": 1.00,
    "total": 10.99,
    "savings": 1.97,
    "item_count": 1
}
```

**Data Structure (Multiple Combos):**
```javascript
{
    "type": "combo_upgraded",
    "items": [/* updated items array with combos */],
    "removed_items": [/* all removed items */],
    "added_combos": [  // Note: plural "combos"
        {
            "name": "Taco Combo",
            "price": 9.99,
            "description": "2 tacos, chips & salsa, and a drink"
        },
        {
            "name": "Burrito Combo",
            "price": 12.99,
            "description": "1 burrito, chips & salsa, and a drink"
        }
    ],
    "subtotal": 22.98,
    "tax": 2.30,
    "total": 25.28,
    "total_savings": 2.95,
    "item_count": 2
}
```

**Frontend Action:** Shows upgrade animation, highlights savings, updates display with combos

---

## Implementation Examples

### Backend (Python)
```python
# Example of sending an event
result.swml_user_event({
    "type": "item_added",
    "items": order_state["items"],
    "item_added": {
        "name": item_data["name"],
        "quantity": quantity
    },
    "order_total": order_state["total"],
    "subtotal": order_state["subtotal"],
    "tax": order_state["tax"],
    "total": order_state["total"]
})
```

### Frontend (JavaScript)
```javascript
// Event handler
function handleUserEvent(event) {
    const eventData = event.detail || event;
    
    switch(eventData.type) {
        case 'item_added':
            orderDisplay.items = eventData.items;
            orderDisplay.subtotal = eventData.subtotal;
            orderDisplay.tax = eventData.tax;
            orderDisplay.total = eventData.total;
            updateOrderDisplay();
            break;
            
        case 'combo_upgraded':
            // Handle both single and multiple combos
            if (eventData.added_combo) {
                // Single combo
                showComboSavings(eventData.savings);
            } else if (eventData.added_combos) {
                // Multiple combos
                showComboSavings(eventData.total_savings);
            }
            orderDisplay.items = eventData.items;
            updateOrderDisplay();
            break;
            
        // ... handle other events
    }
}
```

## Event Flow Diagram

```
Customer Speech → AI Agent → Function Call → Backend Logic
                                    ↓
                            Update Order State
                                    ↓
                            swml_user_event()
                                    ↓
                    WebRTC → Frontend Event Handler
                                    ↓
                            Update UI Display
```

## Best Practices

1. **Always Include Updated State**: Events should include the complete updated state (items array, totals) to ensure UI sync

2. **Consistent Field Names**: Use consistent field names across events (e.g., always `total` not `order_total` in some places)

3. **Atomic Updates**: Each event represents one atomic change to the order

4. **Include Context**: Events should include enough context for the UI to show meaningful feedback (e.g., what was added, what was removed)

5. **Real-time Feedback**: Send events immediately after state changes, not batched

## Testing Events

To test events in the browser console:

```javascript
// Enable debug mode
localStorage.setItem('debug', 'true');

// Monitor all events
client.on('userInput', (event) => {
    console.log('Event received:', event.type, event.detail);
});
```

## Error Handling

If an event is malformed or missing required fields, the frontend should:
1. Log the error to console
2. Attempt graceful degradation (use available fields)
3. Not crash the UI
4. Optionally show a sync warning to user

## Future Enhancements

Potential events to add:
- `order_limit_reached` - When order hits max items/value
- `combo_suggestion_available` - Proactive combo notifications
- `item_out_of_stock` - Inventory integration
- `estimated_wait_time` - Kitchen integration

---

*Last Updated: September 2024*
*Version: 1.0*