<div align="center">
  <img src="logo.png" alt="Holy Guacamole Logo" width="400">
</div>

# Holy Guacamole! - AI-Powered Drive-Thru Implementation Guide

**Live Demo:** [https://holyguacamole.signalwire.me](https://holyguacamole.signalwire.me)

An advanced implementation of a voice-controlled drive-thru ordering system using SignalWire's AI Agent SDK. This guide provides a complete walkthrough of building a production-ready conversational AI agent with state machine architecture, real-time order visualization, and intelligent menu processing.

## Table of Contents
1. [Overview](#overview)
2. [Architecture Deep Dive](#architecture-deep-dive)
3. [Implementation Walkthrough](#implementation-walkthrough)
4. [State Machine Design](#state-machine-design)
5. [SWAIG Functions](#swaig-functions)
6. [Frontend Integration](#frontend-integration)
7. [Deployment](#deployment)
8. [Advanced Features](#advanced-features)

## Overview

Holy Guacamole demonstrates a **code-driven LLM architecture** where application logic controls the AI agent, not the other way around. This approach ensures consistent, reliable, and maintainable conversational experiences.

### Key Differentiators

| Traditional Chatbot | Holy Guacamole (Code-Driven) |
|-------------------|------------------------------|
| LLM controls conversation flow | State machine controls flow |
| Relies on prompt engineering | Logic enforced in code |
| Inconsistent behavior | Deterministic outcomes |
| Hard to debug | Clear execution path |
| LLM must "remember" rules | Rules embedded in functions |

### Core Features
- **Voice-First Interface**: Natural language ordering with Sigmond avatar
- **Real-Time Updates**: Live order display with WebRTC streaming
- **Intelligent Menu Matching**: TF-IDF vector similarity with fallback algorithms
- **Automatic Combo Detection**: Proactive upselling without LLM intervention
- **Production-Ready**: Rate limiting, error handling, order validation

## Architecture Deep Dive

### System Components

```
┌─────────────────────────────────────────────────────────────┐
│                     Customer Browser                        │
│  ┌─────────────────────────────────────────────────────┐    │
│  │           Frontend (simple-client.html)             │    │
│  │  • SignalWire Fabric SDK                            │    │
│  │  • WebRTC Audio/Video                               │    │
│  │  • Real-time Order Display                          │    │
│  └─────────────┬───────────────────────────────────────┘    │
└────────────────┼────────────────────────────────────────────┘
                 │ WebSocket/HTTPS
┌────────────────▼────────────────────────────────────────────┐
│              SignalWire Cloud Infrastructure                │
│  • AI Agent Orchestration                                   │
│  • Speech-to-Text / Text-to-Speech                          │
│  • WebRTC Media Server                                      │
└────────────────┬────────────────────────────────────────────┘
                 │ SWML Protocol
┌────────────────▼────────────────────────────────────────────┐
│            Holy Guacamole Backend (Python)                  │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              State Machine Controller               │    │
│  │  • greeting → taking_order → confirming → complete  │    │
│  └─────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              SWAIG Function Layer                   │    │
│  │  • add_item() • remove_item() • upgrade_to_combo()  │    │
│  └─────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────┐    │
│  │            Intelligence Layer                       │    │
│  │  • TF-IDF Menu Matching                             │    │
│  │  • Combo Detection Algorithm                        │    │
│  │  • Order Validation                                 │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### Code-Driven LLM Pattern

The fundamental principle: **Code drives the LLM, not vice versa**.

```python
# TRADITIONAL APPROACH (Unreliable)
prompt = """
You are a drive-thru assistant.
Remember to:
- Suggest combos when appropriate
- Apply discounts correctly
- Validate order limits
"""
# Problem: LLM might forget, misinterpret, or ignore these rules

# HOLY GUACAMOLE APPROACH (Reliable)
def add_item(args, raw_data):
    # 1. Code validates limits
    if quantity > MAX_ITEMS_PER_TYPE:
        quantity = MAX_ITEMS_PER_TYPE

    # 2. Code detects combos
    combo_suggestion = check_combo_opportunity(items)

    # 3. Code calculates prices
    subtotal = sum(item["total"] for item in items)

    # 4. Return structured response that guides the LLM
    response = f"Added {quantity} {item_data['name']}"
    if combo_suggestion:
        response += f"\n\n{combo_suggestion}"

    result = SwaigFunctionResult(response)
    result.swml_user_event({
        "type": "item_added",
        "items": items
    })
    return result
```

## Implementation Walkthrough

### Step 1: Project Setup

```bash
# Clone and setup
git clone https://github.com/signalwire/sigmond-holyguacamole.git
cd sigmond-holyguacamole

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Step 2: Core Agent Class

The agent uses the `AgentServer` pattern for clean separation of concerns:

```python
from signalwire_agents import AgentBase, AgentServer
from signalwire_agents.core.function_result import SwaigFunctionResult
from pathlib import Path

class HolyGuacamoleAgent(AgentBase):
    def __init__(self):
        super().__init__(
            name="Sigmond",
            route="/swml"  # SWML endpoint
        )

        # Initialize TF-IDF if available
        if HAS_SKLEARN:
            self._initialize_tfidf()

        # Set up personality
        self.prompt_add_section(
            "Personality",
            "You are Sigmond, a friendly drive-thru order taker..."
        )

        # Configure states and functions inline
        contexts = self.define_contexts()
        # ... state configuration ...


def create_server():
    """Create AgentServer with static file mounting."""
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 5000))

    server = AgentServer(host=host, port=port)
    server.register(HolyGuacamoleAgent(), "/swml")

    # Add custom API routes for the web UI
    @server.app.get("/api/menu")
    async def get_menu():
        """Serve the menu data from backend"""
        return {"menu": MENU}

    # Serve static files using SDK's built-in method
    web_dir = Path(__file__).parent / "web"
    if web_dir.exists():
        server.serve_static_files(str(web_dir))

    return server


if __name__ == "__main__":
    server = create_server()
    server.run()
```

### Step 3: State Machine Configuration

```python
def _configure_states(self):
    # Define conversation contexts
    contexts = self.define_contexts()

    default_context = contexts.add_context("default") \
        .add_section("Goal", "Take accurate food orders efficiently")

    # Greeting state - Entry point
    default_context.add_step("greeting") \
        .add_section("Current Task", "Welcome the customer and start their order") \
        .add_bullets("Process", [
            "Welcome them warmly to Holy Guacamole!",
            "Ask what they'd like to order"
        ]) \
        .set_functions(["add_item"]) \
        .set_valid_steps(["taking_order"])

    # Taking order state - Main interaction
    default_context.add_step("taking_order") \
        .set_functions([
            "add_item", "remove_item", "modify_quantity",
            "review_order", "finalize_order", "upgrade_to_combo"
        ]) \
        .set_valid_steps(["confirming_order"])
```

### Step 4: Intelligent Menu Matching

```python
def find_menu_item(item_name):
    """Multi-algorithm menu matching"""
    item_lower = item_name.lower().strip()

    # Algorithm 1: TF-IDF Vector Similarity
    if HAS_SKLEARN and self.vectorizer:
        user_vector = self.vectorizer.transform([item_lower])
        similarities = cosine_similarity(user_vector, self.menu_vectors)[0]
        best_idx = np.argmax(similarities)
        best_score = similarities[best_idx]

        if best_score > 0.42:  # Threshold for accepting a match
            sku, item_data, category = self.sku_map[best_idx]
            return sku, item_data, category

    # Algorithm 2: Alias Matching
    for sku, aliases in MENU_ALIASES.items():
        if item_lower in [alias.lower() for alias in aliases]:
            for category, items in MENU.items():
                if sku in items:
                    return sku, items[sku], category

    # Algorithm 3: Fuzzy String Matching (fallback)
    # Score-based matching with partial word matches...
```

### Step 5: SWAIG Function Implementation

```python
@self.tool(
    name="add_item",
    wait_file="/keyspressing.mp3",
    description="Add an item to the order",
    parameters={
        "type": "object",
        "properties": {
            "item_name": {
                "type": "string",
                "description": "The menu item to add"
            },
            "quantity": {
                "type": "integer",
                "description": "Number of items to add",
                "minimum": 1,
                "maximum": 10
            }
        },
        "required": ["item_name"]
    }
)
def add_item(args, raw_data):
    # 1. Extract order state
    order_state, global_data = get_order_state(raw_data)
    item_name = args["item_name"]
    quantity = args.get("quantity", 1)

    # 2. Apply limits
    MAX_ITEMS_PER_TYPE = 20
    MAX_TOTAL_ITEMS = 50
    if quantity > 10:
        quantity = 10  # Limit per add operation

    # 3. Find item in menu using fuzzy matching
    sku, item_data, category = find_menu_item(item_name)
    if not sku:
        return SwaigFunctionResult(f"I couldn't find '{item_name}' on our menu")

    # 4. Check/update existing items or add new
    existing_item = next((item for item in order_state["items"]
                          if item["sku"] == sku), None)
    if existing_item:
        existing_item["quantity"] += quantity
        existing_item["total"] = existing_item["quantity"] * existing_item["price"]
    else:
        order_state["items"].append({
            "sku": sku,
            "name": item_data["name"],
            "quantity": quantity,
            "price": item_data["price"],
            "total": quantity * item_data["price"]
        })

    # 5. Update totals
    order_state["subtotal"] = sum(item["total"] for item in order_state["items"])
    order_state["tax"] = round(order_state["subtotal"] * 0.10, 2)
    order_state["total"] = order_state["subtotal"] + order_state["tax"]
    order_state["item_count"] = sum(item["quantity"] for item in order_state["items"])

    # 6. Check for combo opportunity (AUTOMATIC!)
    combo_suggestion = check_combo_opportunity(order_state["items"])

    # 7. Build response
    response = f"I've added {quantity} {item_data['name']}"
    response += f" for ${quantity * item_data['price']:.2f}."
    response += f" Your total is now ${order_state['total']:.2f}."
    if combo_suggestion:
        response += f"\n\n{combo_suggestion}"  # Guides LLM to offer upgrade

    # 8. Save state and send real-time update
    result = SwaigFunctionResult(response)
    save_order_state(result, order_state, global_data)
    result.swml_user_event({
        "type": "item_added",
        "items": order_state["items"],
        "subtotal": order_state["subtotal"],
        "tax": order_state["tax"],
        "total": order_state["total"]
    })

    # 9. Auto-transition from greeting to taking_order
    result.swml_change_step("taking_order")

    return result
```

## State Machine Design

### State Transition Diagram

```
┌──────────┐     add_item()      ┌──────────────┐
│ GREETING ├────────────────────►│ TAKING_ORDER │
└──────────┘                     └──────┬───────┘
                                        │
                                    finalize_order()
                                        │
                                        ▼
┌──────────┐   complete_order()   ┌──────────────┐
│ COMPLETE │◄─────────────────────┤  CONFIRMING  │
└──────────┘                      └──────────────┘
```

### State Definitions

#### 1. Greeting State
```python
default_context.add_step("greeting") \
    .add_section("Current Task", "Welcome the customer and start their order") \
    .add_bullets("Process", [
        "Welcome them warmly to Holy Guacamole!",
        "Ask what they'd like to order"
    ]) \
    .set_functions(["add_item"]) \
    .set_valid_steps(["taking_order"])
```

#### 2. Taking Order State
```python
default_context.add_step("taking_order") \
    .add_section("Current Task", "Build the customer's order") \
    .add_bullets("IMPORTANT RULES", [
        "Customer sees their order on screen in real-time",
        "Never read back the entire order unprompted",
        "If add_item response includes 'Great news!' about combo:",
        "  - If customer says 'yes/sure/okay': CALL upgrade_to_combo",
        "  - If customer says 'no': Continue with regular order"
    ]) \
    .set_functions(["add_item", "remove_item", "modify_quantity",
                   "review_order", "finalize_order", "upgrade_to_combo"]) \
    .set_valid_steps(["confirming_order"])
```

#### 3. Confirming Order State
```python
default_context.add_step("confirming_order") \
    .add_section("Current Task", "Confirm the order before payment") \
    .add_bullets("Process", [
        "Customer can see their complete order on screen",
        "Confirm they're ready to pay"
    ]) \
    .set_functions(["process_payment", "add_item",
                   "remove_item", "cancel_order"]) \
    .set_valid_steps(["payment_processing"])
```

## SWAIG Functions

### Complete Function Reference

| Function | Purpose | State Transitions |
|----------|---------|-------------------|
| `add_item` | Add items to order | greeting -> taking_order |
| `remove_item` | Remove items using fuzzy matching | None |
| `modify_quantity` | Change item quantity | None |
| `review_order` | Display current order | None |
| `finalize_order` | Move to confirmation | taking_order -> confirming |
| `upgrade_to_combo` | Replace items with combo | None |
| `process_payment` | Confirm and pay | confirming -> payment |
| `complete_order` | Generate order number | payment -> complete |
| `cancel_order` | Clear and restart | any -> greeting |
| `new_order` | Start fresh order | complete -> greeting |

### Combo Detection Algorithm

```python
def check_combo_opportunity(items):
    """Automatic combo detection without LLM awareness"""
    if not items:
        return None

    # Count actual quantities of each item type
    taco_count = sum(item["quantity"] for item in items
                     if "taco" in item["name"].lower())
    burrito_count = sum(item["quantity"] for item in items
                        if "burrito" in item["name"].lower())
    chips_count = sum(item["quantity"] for item in items
                      if "chips" in item["name"].lower()
                      and "salsa" in item["name"].lower())
    drink_count = sum(item["quantity"] for item in items
                      if "small" in item["name"].lower()
                      and "drink" in item["name"].lower())

    # Check for taco combo (2 tacos + 1 chips + 1 drink)
    if taco_count >= 2 and chips_count >= 1 and drink_count >= 1:
        taco_price = 3.49 * 2
        chips_price = 2.99
        drink_price = 1.99
        current_total = taco_price + chips_price + drink_price  # $11.96
        combo_price = 9.99
        savings = round(current_total - combo_price, 2)  # $1.97
        return (f"Great news! I can upgrade your 2 tacos, chips & salsa, "
               f"and drink to a Taco Combo and save you ${savings:.2f}!")

    return None  # No combo opportunity
```

## Frontend Integration

### WebRTC Connection Setup

```javascript
// In your frontend JavaScript
async function connectToAgent() {
    const client = await SignalWire.SignalWireClient({
        token: STATIC_TOKEN,
        fabric: { audio: true, video: true }
    });

    // Handle real-time events
    client.on('userInput', handleUserEvent);

    // Dial the AI agent
    const call = await client.dial({
        to: DESTINATION,
        nodeId: await client.getNodeId(),
        rootElement: document.getElementById('video-container'),
        applyLocalVideoOverlay: false,
        userVariables: {
            userName: "Customer"
        }
    });
}
```

### Real-Time Event Handling

```javascript
function handleUserEvent(event) {
    const { type, items, subtotal, tax, total } = event.detail;

    switch(type) {
        case 'item_added':
        case 'item_removed':
            updateOrderDisplay(items);
            updateTotals(subtotal, tax, total);
            break;

        case 'combo_upgraded':
            showComboAnimation();
            updateOrderDisplay(items);
            break;

        case 'order_complete':
            showOrderNumber(event.detail.order_number);
            break;
    }
}
```

### Order Display Implementation

```javascript
function updateOrderDisplay(items) {
    const container = document.getElementById('order-items');

    if (!items || items.length === 0) {
        container.innerHTML = '<div class="empty">Your order will appear here</div>';
        return;
    }

    container.innerHTML = items.map(item => `
        <div class="order-item">
            <span class="quantity">${item.quantity}x</span>
            <span class="name">${item.name}</span>
            <span class="price">$${item.total.toFixed(2)}</span>
        </div>
    `).join('');

    // Auto-scroll to latest
    requestAnimationFrame(() => {
        container.scrollTop = container.scrollHeight;
    });
}
```

## Deployment

### Project Structure

```
holyguacamole/
├── holy_guacamole.py      # Main agent with AgentServer
├── requirements.txt       # Python dependencies
├── Procfile               # For Dokku/Heroku deployment
└── web/
    ├── index.html         # Served at /
    ├── simple-client.html # Main UI
    ├── styles.css         # Stylesheets
    ├── app.js             # Frontend JavaScript
    ├── sigmond_cc_idle.mp4    # Avatar idle video
    └── sigmond_cc_talking.mp4 # Avatar talking video
```

### Route Priority

The AgentServer handles routes in this priority:
1. `/swml/*` - HolyGuacamoleAgent (SWML/SWAIG endpoints)
2. `/api/menu` - Custom menu API endpoint
3. `/health` - AgentServer health check
4. `/*` - Static files from `web/` directory

### Local Development

```bash
# Basic startup
python holy_guacamole.py

# With environment variables
export PORT=5000
export HOST=0.0.0.0
python holy_guacamole.py
```

### Production Deployment (Dokku)

```bash
# 1. Create Procfile (already included)
web: python holy_guacamole.py

# 2. Deploy to Dokku
git remote add dokku dokku@your-server:holyguacamole
git push dokku main

# 3. Configure environment
dokku config:set holyguacamole SWML_BASIC_AUTH_USER=xxx
dokku config:set holyguacamole SWML_BASIC_AUTH_PASSWORD=xxx

# 4. SSL/Domain setup
dokku domains:add holyguacamole holyguacamole.signalwire.me
dokku letsencrypt:enable holyguacamole
```

## Advanced Features

### 1. Order Protection Limits

```python
# Configurable limits
MAX_ITEMS_PER_TYPE = 20   # Max 20 of same item
MAX_TOTAL_ITEMS = 50      # Max 50 items total
MAX_ORDER_VALUE = 500.00  # Max $500 order

def validate_order_limits(order_state, new_quantity):
    """Enforce business rules at code level"""
    if order_state["item_count"] + new_quantity > MAX_TOTAL_ITEMS:
        return False, "order limit reached"

    if order_state["total"] > MAX_ORDER_VALUE:
        return False, "maximum order value exceeded"

    return True, None
```

### 2. Natural Language Price Conversion

```python
def dollars_to_words(amount):
    """Convert $13.50 to 'thirteen dollars and fifty cents'"""
    # Converts numeric prices to spoken English
    # Used for TTS to read prices naturally
```

### 3. Multi-Item Processing

The agent handles multiple items in one sentence:
- "two tacos and a drink" -> calls `add_item` twice
- Prompt instructs LLM to process each item separately

### 4. Error Recovery

```python
def handle_menu_not_found(item_name, order_state):
    """Graceful handling of unrecognized items"""
    return f"I couldn't find '{item_name}' on our menu. Please check the menu on your screen."
```

## Testing & Debugging

### Enable Debug Mode

```python
# Debug output is enabled by default for menu matching
print(f"[DEBUG] Searching for: '{item_name}'")
print(f"[DEBUG] TF-IDF best match: {item_data['name']} (score: {best_score:.3f})")
```

### Common Issues & Solutions

| Issue | Cause | Solution |
|-------|-------|----------|
| Items not recognized | Poor TF-IDF match | Add aliases in MENU_ALIASES |
| Video not displaying | CORS/URL issues | Check on_swml_request host detection |
| State transitions fail | Missing context change | Ensure swml_change_step is called |
| Combo not detected | Item categorization | Verify SKU category mapping |
| Events not reaching UI | Missing user_event | Check swml_user_event calls |

## Conclusion

Holy Guacamole demonstrates how to build production-ready conversational AI by:

1. **Controlling the LLM** through state machines rather than letting it control the flow
2. **Embedding business logic** in code where it's testable and maintainable
3. **Providing real-time feedback** through WebRTC event streaming
4. **Handling edge cases** gracefully with fallback algorithms
5. **Scaling reliably** with deterministic, code-driven behavior

The result is an AI agent that behaves consistently, handles complex scenarios, and provides an excellent user experience.

## Resources

- **Live Demo**: [https://holyguacamole.signalwire.me](https://holyguacamole.signalwire.me)
- **SignalWire Docs**: [developer.signalwire.com](https://developer.signalwire.com)
- **AI Agent SDK**: [github.com/signalwire/signalwire-agents](https://github.com/signalwire/signalwire-agents)
- **Support**: Open an issue on GitHub

## License

MIT License - See LICENSE file for details

---
*Built with SignalWire AI Agent SDK*
