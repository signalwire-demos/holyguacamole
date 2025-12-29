#!/usr/bin/env python3
"""
Holy Guacamole! - AI-Powered Drive-Thru Order Agent
Web UI and SWML served on the same port
"""

import random
import os
from pathlib import Path
from signalwire_agents import AgentBase, AgentServer
from signalwire_agents.core.function_result import SwaigFunctionResult

# Import for TF-IDF vector matching
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    import numpy as np
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False
    print("Warning: scikit-learn not installed. Falling back to fuzzy matching.")

# Phase 1: Simple menu structure with descriptions
MENU = {
    "tacos": {
        "T001": {"name": "Beef Taco", "price": 3.49, "description": "Seasoned ground beef, lettuce, cheese, and salsa in a crispy shell"},
        "T002": {"name": "Chicken Taco", "price": 3.49, "description": "Grilled chicken, lettuce, cheese, and pico de gallo in a crispy shell"},
        "T003": {"name": "Bean Taco", "price": 2.99, "description": "Refried beans, lettuce, cheese, and salsa in a crispy shell"}
    },
    "burritos": {
        "B001": {"name": "Beef Burrito", "price": 8.99, "description": "Large flour tortilla with seasoned beef, rice, beans, cheese, and salsa"},
        "B002": {"name": "Chicken Burrito", "price": 8.99, "description": "Large flour tortilla with grilled chicken, rice, beans, cheese, and pico"},
        "B003": {"name": "Bean & Cheese Burrito", "price": 6.99, "description": "Large flour tortilla with refried beans and melted cheese"}
    },
    "quesadillas": {
        "Q001": {"name": "Cheese Quesadilla", "price": 5.99, "description": "Grilled flour tortilla with melted cheese blend"},
        "Q002": {"name": "Chicken Quesadilla", "price": 7.99, "description": "Grilled flour tortilla with seasoned chicken and melted cheese"}
    },
    "sides": {
        "S001": {"name": "Chips & Salsa", "price": 2.99, "description": "Fresh tortilla chips with our house-made salsa"},
        "S002": {"name": "Chips & Guacamole", "price": 4.99, "description": "Fresh tortilla chips with fresh-made guacamole"}
    },
    "drinks": {
        "D001": {"name": "Small Drink", "price": 1.99, "description": "16oz fountain drink of your choice"},
        "D002": {"name": "Large Drink", "price": 2.99, "description": "24oz fountain drink of your choice"},
        "D003": {"name": "Bottled Water", "price": 1.99, "description": "16oz bottled water"}
    },
    "combos": {
        "C001": {"name": "Taco Combo", "price": 9.99, "description": "2 tacos (your choice) + chips & salsa + small drink"},
        "C002": {"name": "Burrito Combo", "price": 12.99, "description": "Any burrito + chips & salsa + small drink"}
    }
}

# Alias dictionary for better menu item matching
MENU_ALIASES = {
    "D003": ["water", "bottled water", "water bottle", "aqua", "bottle of water", "h2o"],
    "D001": ["small soda", "small drink", "soda", "soft drink", "small fountain drink", "coke", "pepsi", "sprite"],
    "D002": ["large soda", "large drink", "big drink", "large fountain drink", "big soda"],
    "Q001": ["quesadilla", "cheese quesadilla", "plain quesadilla", "just cheese", "cheese only"],
    "Q002": ["chicken quesadilla", "chicken and cheese quesadilla"],
    "C001": ["taco meal", "taco combo", "taco deal", "taco special", "combo taco"],
    "C002": ["burrito meal", "burrito combo", "burrito deal", "burrito special", "combo burrito"],
    "S001": ["chips", "nachos", "chips and salsa", "chips with salsa", "salsa and chips", "just chips", "salsa", "chips n salsa"],
    "S002": ["guac", "chips and guac", "chips with guacamole", "guacamole and chips", "guac and chips", "guacamole", "chips n guacamole"],
    "T001": ["beef taco", "beef tacos", "ground beef taco", "regular taco", "taco beef"],
    "T002": ["chicken taco", "chicken tacos", "grilled chicken taco", "taco chicken"],
    "T003": ["bean taco", "bean tacos", "vegetarian taco", "veggie taco"],
    "B001": ["beef burrito", "beef burritos", "regular burrito", "burrito beef", "burrito with beef"],
    "B002": ["chicken burrito", "chicken burritos", "burrito chicken", "burrito with chicken"],
    "B003": ["bean burrito", "bean burritos", "bean and cheese burrito", "cheese and bean burrito"]
}

class HolyGuacamoleAgent(AgentBase):
    """Sigmond - Your Holy Guacamole! Order Assistant"""
    
    def __init__(self):
        super().__init__(
            name="Sigmond",
            route="/swml",  # SWML endpoint path
            record_call=True
        )
        
        # Initialize TF-IDF vectorizer if available
        self.vectorizer = None
        self.menu_vectors = None
        self.sku_map = []
        
        if HAS_SKLEARN:
            self._initialize_tfidf()
        

        self.prompt_add_section(
            "Personality",
            "You are Sigmond, the friendly order-taker at Holy Guacamole! Mexican drive-thru. "
            "You're warm, enthusiastic about the food, and help customers order efficiently. "
            "The customer has a screen showing their order, so NEVER read back the full order - they can see it! "
            "Just acknowledge items briefly as they're added. Keep responses concise and friendly. "
            "CRITICAL: When a customer orders multiple items in one sentence (like 'two tacos and a drink'), "
            "you MUST call add_item separately for EACH item. Never skip items! "
            "IMPORTANT MENU RULE: NEVER list specific menu items or say what drinks/options we have. "
            "If asked about menu items or options, say 'Please check the menu on your screen' or 'Everything we have is shown on the menu.' "
            "You can ONLY confirm what we have by attempting to add_item - let the function tell you if we have it or not."
        )
        
        # Define conversation contexts with state machine
        contexts = self.define_contexts()
        
        default_context = contexts.add_context("default") \
            .add_section("Goal", "Take accurate food orders efficiently while providing excellent customer service.")
        
        # GREETING STATE - Entry point
        default_context.add_step("greeting") \
            .add_section("Current Task", "Welcome the customer and start their order") \
            .add_bullets("Process", [
                "Welcome them warmly to Holy Guacamole!",
                "Ask what they'd like to order",
                "Mention combo meals save money",
                "Listen for ALL items they mention",
                "If they order multiple items (e.g. 'two tacos and a drink'), call add_item for EACH item separately"
            ]) \
            .set_step_criteria("Customer has started ordering") \
            .set_functions(["add_item"]) \
            .set_valid_steps(["taking_order"])
        
        # TAKING ORDER STATE - Main ordering phase  
        default_context.add_step("taking_order") \
            .add_section("Current Task", "Build the customer's order") \
            .add_bullets("IMPORTANT RULES", [
                "Current order has ${global_data.order_state.item_count} items",
                "Current total: $${global_data.order_state.total}",
                "🔴 HIGHEST PRIORITY - Check for RESTART patterns FIRST:",
                "  - 'I only want X', 'never mind just X', 'actually just X' = cancel_order() THEN add_item(X)",
                "  - 'cancel', 'start over', 'never mind' = cancel_order()",
                "When customer orders multiple items (e.g. 'two tacos and a drink'): CALL add_item FOR EACH ITEM SEPARATELY",
                "CRITICAL: If customer says 'X and Y', you MUST call add_item twice - once for X and once for Y",
                "⚠️ CRITICAL PATTERN - Customer wants to RESTART with only one item:",
                "  - TRIGGERS: 'never mind, I just want X', 'I only want X', 'forget everything, just X'",
                "  - Also: 'actually just give me X', 'you know what, just X', 'scratch that, only X'",
                "  - This means CLEAR ALL and keep ONLY the mentioned item",
                "  - ACTION REQUIRED: 1) FIRST call cancel_order(), 2) THEN call add_item(X)",
                "  - DO NOT use remove_item - MUST use cancel_order to clear everything",
                "When customer explicitly wants to cancel entire order:",
                "  - 'cancel my order', 'start over', 'never mind' (without mentioning another item)",
                "  - ACTION: CALL cancel_order()",
                "When customer wants to remove items:",
                "  - 'remove one water/bottle': CALL remove_item('water', quantity=1)",
                "  - 'remove 5 waters/bottles': CALL remove_item('water', quantity=5)",
                "  - 'remove all the water/bottles': CALL remove_item('water', quantity=-1)",
                "  - Default (no quantity specified): removes 1 item",
                "  - IMPORTANT: 'bottles' usually means 'water' - use 'water' as item_name",
                "When customer wants to change quantity: CALL modify_quantity function",
                "When customer wants to see order: CALL review_order function",
                "When customer is done: CALL finalize_order function",
                "Acknowledge items briefly (don't read back the entire order)",
                "💡 COMBO UPGRADES: If add_item response includes 'Great news!' about a combo:",
                "  - This means a money-saving combo is available",
                "  - If customer says 'yes', 'sure', 'okay', 'upgrade' or agrees: CALL upgrade_to_combo",
                "  - Determine combo type from the suggestion (taco, burrito, or both)",
                "  - If response mentions TWO combos, use combo_type='both'",
                "NEVER quote prices yourself - let the functions provide them"
            ]) \
            .set_step_criteria("Customer says they're done ordering") \
            .set_functions(["add_item", "remove_item", "modify_quantity", "review_order", "finalize_order", "upgrade_to_combo", "cancel_order"]) \
            .set_valid_steps(["confirming_order"])
        
        # CONFIRMING ORDER STATE
        default_context.add_step("confirming_order") \
            .add_section("Current Task", "Confirm the complete order") \
            .add_bullets("Instructions", [
                "The customer can see their order on the screen",
                "DO NOT read back the items - they can see them",
                "Just ask if the order on screen looks correct",
                "If they confirm, use process_payment",
                "If they want changes, use add_item or remove_item",
                "Only mention the total price, not individual items"
            ]) \
            .set_step_criteria("Order is confirmed as correct") \
            .set_functions(["process_payment", "add_item", "remove_item", "cancel_order"]) \
            .set_valid_steps(["payment_processing", "taking_order"])
        
        # PAYMENT PROCESSING STATE
        default_context.add_step("payment_processing") \
            .add_section("Current Task", "Direct customer to payment") \
            .add_bullets("Instructions", [
                "Order number: ${global_data.order_state.order_number}",
                "Total: $${global_data.order_state.total}",
                "Tell them to pull forward to the first window",
                "Thank them for their order",
                "Call complete_order to finish"
            ]) \
            .set_step_criteria("Payment instructions given") \
            .set_functions(["complete_order"]) \
            .set_valid_steps(["order_complete"])
        
        # ORDER COMPLETE STATE
        default_context.add_step("order_complete") \
            .add_section("Current Task", "Order is complete") \
            .add_bullets("Final Steps", [
                "Thank the customer",
                "Wish them a great day",
                "If they want another order, use new_order"
            ]) \
            .set_functions(["new_order"]) \
            .set_valid_steps(["greeting"])
        
        # Helper functions
        def get_order_state(raw_data):
            """Get current order state"""
            global_data = raw_data.get('global_data', {})
            
            default = {
                "items": [],  # List of {sku, name, quantity, price, total}
                "total": 0.00,
                "subtotal": 0.00,
                "tax": 0.00,
                "order_number": None,
                "item_count": 0
            }
            
            return global_data.get('order_state', default), global_data
        
        def save_order_state(result, order_state, global_data):
            """Save order state and send to frontend"""
            global_data['order_state'] = order_state
            result.update_global_data(global_data)
            return result
        
        def calculate_totals(items):
            """Calculate order totals with tax"""
            subtotal = round(sum(item["total"] for item in items), 2)
            tax = round(subtotal * 0.10, 2)  # 10% tax
            total = round(subtotal + tax, 2)
            return subtotal, tax, total
        
        def order_number_to_words(number):
            """Convert order number to individual spoken digits (e.g., 401 -> 'four zero one')"""
            digit_words = {
                '0': 'zero', '1': 'one', '2': 'two', '3': 'three', '4': 'four',
                '5': 'five', '6': 'six', '7': 'seven', '8': 'eight', '9': 'nine'
            }
            
            # Convert number to string and spell out each digit
            digits = str(number)
            spoken_digits = [digit_words[d] for d in digits]
            return ' '.join(spoken_digits)
        
        def dollars_to_words(amount):
            """Convert dollar amount to spoken English"""
            # Handle zero
            if amount == 0:
                return "zero dollars"
            
            # Split into dollars and cents
            dollars = int(amount)
            cents = round((amount - dollars) * 100)
            
            # Number words
            ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
            teens = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", 
                    "sixteen", "seventeen", "eighteen", "nineteen"]
            tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
            
            def number_to_words(n):
                """Convert number under 1000 to words"""
                if n == 0:
                    return ""
                elif n < 10:
                    return ones[n]
                elif n < 20:
                    return teens[n-10]
                elif n < 100:
                    return tens[n//10] + ("-" + ones[n%10] if n%10 > 0 else "")
                else:
                    hundred_part = ones[n//100] + " hundred"
                    remainder = n % 100
                    if remainder == 0:
                        return hundred_part
                    elif remainder < 10:
                        return hundred_part + " and " + ones[remainder]
                    elif remainder < 20:
                        return hundred_part + " and " + teens[remainder-10]
                    else:
                        return hundred_part + " and " + tens[remainder//10] + ("-" + ones[remainder%10] if remainder%10 > 0 else "")
            
            # Build the result
            result = []
            
            # Handle thousands
            if dollars >= 1000:
                thousands = dollars // 1000
                result.append(number_to_words(thousands) + " thousand")
                dollars = dollars % 1000
            
            # Handle hundreds and below
            if dollars > 0:
                result.append(number_to_words(dollars))
            
            # Add "dollar(s)"
            if result:
                dollar_amount = " ".join(result)
                if dollar_amount == "one":
                    result = ["one dollar"]
                else:
                    result = [" ".join(result) + " dollars"]
            else:
                result = []
            
            # Handle cents
            if cents > 0:
                if cents == 1:
                    cent_str = "one cent"
                else:
                    cent_str = number_to_words(cents) + " cents"
                
                if result:
                    result.append("and " + cent_str)
                else:
                    result = [cent_str]
            
            return " ".join(result) if result else "zero dollars"
        
        def check_combo_opportunity(items):
            """Check if current order qualifies for a combo upgrade"""
            if not items:
                return None
            
            # Count actual quantities of each item type
            taco_count = sum(item["quantity"] for item in items if "taco" in item["name"].lower() and "combo" not in item["name"].lower())
            burrito_count = sum(item["quantity"] for item in items if "burrito" in item["name"].lower() and "combo" not in item["name"].lower())
            chips_count = sum(item["quantity"] for item in items if "chips" in item["name"].lower() and "salsa" in item["name"].lower() and "combo" not in item["name"].lower())
            drink_count = sum(item["quantity"] for item in items if "small" in item["name"].lower() and "drink" in item["name"].lower() and "combo" not in item["name"].lower())
            
            # Check what combos we already have
            taco_combo_count = sum(item["quantity"] for item in items if "taco combo" in item["name"].lower())
            burrito_combo_count = sum(item["quantity"] for item in items if "burrito combo" in item["name"].lower())
            
            # Don't suggest upgrades for items already in combos
            # But allow suggesting different combo types
            
            # Check both combo opportunities and suggest the best one
            suggestions = []
            
            # Check for taco combo (2 tacos + 1 chips + 1 drink) - only if we don't already have taco combos
            if taco_combo_count == 0 and taco_count >= 2 and chips_count >= 1 and drink_count >= 1:
                taco_price = 3.49 * 2
                chips_price = 2.99
                drink_price = 1.99
                current_total = taco_price + chips_price + drink_price  # $11.96
                combo_price = 9.99
                savings = round(current_total - combo_price, 2)  # $1.97
                suggestions.append(("taco", savings, f"💡 Great news! I can upgrade your 2 tacos, chips & salsa, and drink to a Taco Combo and save you {dollars_to_words(savings)}!"))
            
            # Check for burrito combo (1 burrito + 1 chips + 1 drink) - only if we don't already have burrito combos
            # Check if we have ADDITIONAL items for burrito combo beyond taco combo suggestion
            # If taco combo uses 1 chips and 1 drink, we need 2 total chips and 2 drinks for both combos
            min_chips_for_burrito = 2 if (taco_count >= 2 and len(suggestions) > 0) else 1
            min_drinks_for_burrito = 2 if (taco_count >= 2 and len(suggestions) > 0) else 1
            
            if burrito_combo_count == 0 and burrito_count >= 1 and chips_count >= min_chips_for_burrito and drink_count >= min_drinks_for_burrito:
                burrito_price = 8.99
                chips_price = 2.99
                drink_price = 1.99
                current_total = burrito_price + chips_price + drink_price  # $13.97
                combo_price = 12.99
                savings = round(current_total - combo_price, 2)  # $0.98
                suggestions.append(("burrito", savings, f"💡 Great news! I can upgrade your burrito, chips & salsa, and drink to a Burrito Combo and save you {dollars_to_words(savings)}!"))
            
            # If we have multiple combo opportunities, suggest both!
            if len(suggestions) == 2:
                total_savings = suggestions[0][1] + suggestions[1][1]
                return f"💡 Amazing! You qualify for TWO combo upgrades! I can upgrade your tacos AND burrito meals to combos, saving you a total of {dollars_to_words(total_savings)}! Just say 'yes' or 'upgrade both' to save money."
            elif len(suggestions) == 1:
                return suggestions[0][2] + " Just say 'yes' to save money."
            
            return None
        
        def find_menu_item(item_name):
            """Find item in menu by name with TF-IDF vector matching or fuzzy matching"""
            item_lower = item_name.lower().strip()
            print(f"[DEBUG] Searching for: '{item_name}' (normalized: '{item_lower}')")
            
            # First check exact match with menu item names
            for category, items in MENU.items():
                for sku, item_data in items.items():
                    if item_lower == item_data["name"].lower():
                        print(f"[DEBUG] Exact match found: {item_data['name']} (SKU: {sku})")
                        return sku, item_data, category
            
            # Check aliases for exact match
            for sku, aliases in MENU_ALIASES.items():
                for alias in aliases:
                    if item_lower == alias.lower():
                        print(f"[DEBUG] Alias match found: '{alias}' -> SKU: {sku}")
                        # Find the item data from the SKU
                        for category, items in MENU.items():
                            if sku in items:
                                print(f"[DEBUG] Resolved to: {items[sku]['name']}")
                                return sku, items[sku], category
            
            # Try TF-IDF matching if no exact match found
            if HAS_SKLEARN and self.vectorizer and self.menu_vectors is not None:
                try:
                    # Vectorize the user input
                    user_vector = self.vectorizer.transform([item_lower])
                    
                    # Calculate cosine similarities
                    similarities = cosine_similarity(user_vector, self.menu_vectors)[0]
                    
                    # Get the best match
                    best_idx = np.argmax(similarities)
                    best_score = similarities[best_idx]
                    
                    print(f"[DEBUG] TF-IDF best match: {self.sku_map[best_idx][1]['name']} (score: {best_score:.3f})")
                    
                    # Return if similarity is high enough
                    # Threshold set to 0.42 for better balance between accuracy and flexibility
                    if best_score > 0.42:  # Threshold above 0.4 as requested
                        sku, item_data, category = self.sku_map[best_idx]
                        print(f"[DEBUG] TF-IDF match accepted: {item_data['name']} (SKU: {sku})")
                        return sku, item_data, category
                    else:
                        print(f"[DEBUG] TF-IDF score too low ({best_score:.3f} < 0.42), no match found")
                        # When TF-IDF is enabled, don't fall back to fuzzy matching
                        return None, None, None
                except Exception as e:
                    # If TF-IDF fails due to error, also don't fall back
                    print(f"[DEBUG] TF-IDF matching failed: {e}, no match found")
                    return None, None, None
            
            # Fallback to fuzzy matching (only if TF-IDF is not available)
            # Remove common words
            item_clean = item_lower.replace("the ", "").replace("a ", "").replace("an ", "").replace("just ", "").replace("plain ", "").strip()
            print(f"[DEBUG] Fuzzy matching with cleaned: '{item_clean}'")
            
            # Score-based matching
            best_match = None
            best_score = 0
            
            for category, items in MENU.items():
                for sku, item_data in items.items():
                    score = 0
                    item_name_lower = item_data["name"].lower()
                    
                    # Check if all words in input are in item name
                    input_words = item_clean.split()
                    if all(word in item_name_lower for word in input_words):
                        score += 80
                    
                    # Check aliases for partial matches
                    if sku in MENU_ALIASES:
                        for alias in MENU_ALIASES[sku]:
                            if item_clean in alias.lower():
                                score += 70
                                break
                            elif any(word in alias.lower() for word in input_words):
                                score += 40
                    
                    # Special cases for common requests
                    if "quesadilla" in item_clean and "quesadilla" in item_name_lower:
                        score += 50
                        if "cheese" not in item_clean and "cheese" in item_name_lower and "chicken" not in item_name_lower:
                            # Default to cheese quesadilla if just "quesadilla"
                            score += 30
                    
                    if "water" in item_clean and "water" in item_name_lower:
                        score += 90  # High score for water match
                    
                    if "combo" in item_clean and "combo" in item_name_lower:
                        score += 60
                        if "taco" in item_clean and "taco" in item_name_lower:
                            score += 40
                        elif "burrito" in item_clean and "burrito" in item_name_lower:
                            score += 40
                    
                    if "drink" in item_clean or "soda" in item_clean:
                        if "drink" in item_name_lower:
                            score += 50
                            if "small" in item_clean and "small" in item_name_lower:
                                score += 40
                            elif "large" in item_clean and "large" in item_name_lower:
                                score += 40
                            elif "small" not in item_clean and "large" not in item_clean:
                                # Default to small drink if size not specified
                                if "small" in item_name_lower:
                                    score += 20
                    
                    if "chips" in item_clean and "chips" in item_name_lower:
                        score += 50
                        if "guac" in item_clean and "guacamole" in item_name_lower:
                            score += 40
                        elif "salsa" in item_clean and "salsa" in item_name_lower:
                            score += 40
                        elif "salsa" not in item_clean and "guac" not in item_clean:
                            # Default to chips & salsa if not specified
                            if "salsa" in item_name_lower:
                                score += 20
                    
                    # Update best match if this score is higher
                    if score > best_score:
                        best_score = score
                        best_match = (sku, item_data, category)
            
            # Return best match if score is high enough
            if best_score >= 40:
                sku, item_data, category = best_match
                print(f"[DEBUG] Fuzzy match found: {item_data['name']} (SKU: {sku}, score: {best_score})")
                return best_match
            
            print(f"[DEBUG] No match found for '{item_name}'")
            return (None, None, None)
        
        # Core ordering functions
        @self.tool(
            name="add_item",
            wait_file="/keyspressing.mp3",
            description="Add an item to the order",
            parameters={
                "type": "object",
                "properties": {
                    "item_name": {
                        "type": "string",
                        "description": "Name of the menu item"
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "How many to add",
                        "minimum": 1,
                        "maximum": 10
                    }
                },
                "required": ["item_name"]
            }
        )
        def add_item(args, raw_data):
            """Add item to order"""
            order_state, global_data = get_order_state(raw_data)
            item_name = args["item_name"]
            quantity = args.get("quantity", 1)
            
            # Enforce reasonable limits
            MAX_ITEMS_PER_TYPE = 20  # Max 20 of any single item
            MAX_TOTAL_ITEMS = 50     # Max 50 items total in order
            MAX_ORDER_VALUE = 500.00  # Max $500 order value
            
            # Validate quantity
            if quantity > 10:
                quantity = 10
                limited_message = f" (Limited to 10 per add)"
            else:
                limited_message = ""
            
            # Find the item in menu
            sku, item_data, category = find_menu_item(item_name)
            
            if not sku:
                return SwaigFunctionResult(f"I couldn't find '{item_name}' on our menu. Please check the menu on your screen for available items.")
            
            # Check current total items
            current_total_items = sum(item["quantity"] for item in order_state["items"])
            if current_total_items >= MAX_TOTAL_ITEMS:
                return SwaigFunctionResult(f"Your order already has {current_total_items} items, which is our maximum. Please remove some items if you'd like to add more.")
            
            # Check if adding would exceed total limit
            if current_total_items + quantity > MAX_TOTAL_ITEMS:
                quantity = MAX_TOTAL_ITEMS - current_total_items
                limited_message = f" (Limited to {quantity} to stay within {MAX_TOTAL_ITEMS} item maximum)"
            
            # Check if item already in order
            existing_item = None
            for order_item in order_state["items"]:
                if order_item["sku"] == sku:
                    existing_item = order_item
                    break
            
            # Check per-item limit
            if existing_item:
                new_quantity = existing_item["quantity"] + quantity
                if new_quantity > MAX_ITEMS_PER_TYPE:
                    allowed_add = MAX_ITEMS_PER_TYPE - existing_item["quantity"]
                    if allowed_add <= 0:
                        return SwaigFunctionResult(f"You already have {existing_item['quantity']} {item_data['name']}s, which is the maximum of {MAX_ITEMS_PER_TYPE} per item type.")
                    quantity = allowed_add
                    new_quantity = MAX_ITEMS_PER_TYPE
                    limited_message = f" (Limited to {MAX_ITEMS_PER_TYPE} total per item type)"
            else:
                if quantity > MAX_ITEMS_PER_TYPE:
                    quantity = MAX_ITEMS_PER_TYPE
                    limited_message = f" (Limited to {MAX_ITEMS_PER_TYPE} per item type)"
            
            # Check if order would exceed max value
            potential_subtotal = sum(item["total"] for item in order_state["items"]) + (item_data["price"] * quantity)
            if potential_subtotal > MAX_ORDER_VALUE:
                # Calculate how many we can actually add
                remaining_value = MAX_ORDER_VALUE - sum(item["total"] for item in order_state["items"])
                max_quantity_by_value = int(remaining_value / item_data["price"])
                if max_quantity_by_value <= 0:
                    return SwaigFunctionResult(f"Adding this would exceed our {dollars_to_words(MAX_ORDER_VALUE)} order limit. Your current subtotal is {dollars_to_words(order_state['subtotal'])}.")
                quantity = min(quantity, max_quantity_by_value)
                limited_message = f" (Limited to {quantity} to stay within ${MAX_ORDER_VALUE} order limit)"
            
            if existing_item:
                # Update quantity
                existing_item["quantity"] += quantity
                existing_item["total"] = round(existing_item["price"] * existing_item["quantity"], 2)
                response = f"Updated {item_data['name']} - now you have {existing_item['quantity']}{limited_message}."
            else:
                # Add new item
                new_item = {
                    "sku": sku,
                    "name": item_data["name"],
                    "description": item_data.get("description", ""),
                    "price": item_data["price"],
                    "quantity": quantity,
                    "total": round(item_data["price"] * quantity, 2)
                }
                order_state["items"].append(new_item)
                
                if quantity > 1:
                    response = f"Added {quantity} {item_data['name']}s to your order{limited_message}."
                else:
                    response = f"Added {item_data['name']} to your order{limited_message}."
            
            # Calculate new totals
            order_state["subtotal"], order_state["tax"], order_state["total"] = calculate_totals(order_state["items"])
            order_state["item_count"] = sum(item["quantity"] for item in order_state["items"])
            
            # Check for combo opportunities after adding item
            combo_suggestion = check_combo_opportunity(order_state["items"])
            
            response += f" Your total is now {dollars_to_words(order_state['total'])}."
            
            # Add combo suggestion if found
            if combo_suggestion:
                response += f"\n\n{combo_suggestion}"
            
            result = SwaigFunctionResult(response)
            save_order_state(result, order_state, global_data)
            
            # Send event to UI with all calculated values
            # Get the actual item from order_state to have correct quantity
            final_item = None
            for order_item in order_state["items"]:
                if order_item["sku"] == sku:
                    final_item = order_item
                    break
            
            event_data = {
                "type": "item_added",
                "item": {
                    "sku": sku,
                    "name": item_data["name"],
                    "description": item_data.get("description", ""),
                    "quantity": final_item["quantity"] if final_item else quantity,
                    "price": item_data["price"],
                    "total": final_item["total"] if final_item else round(item_data["price"] * quantity, 2)
                },
                "order_total": order_state["total"],
                "subtotal": order_state["subtotal"],
                "tax": order_state["tax"],
                "item_count": order_state["item_count"]
            }
            result.swml_user_event(event_data)
            
            # Change to taking_order if we're in greeting
            result.swml_change_step("taking_order")
            
            return result
        
        @self.tool(
            name="remove_item",
            wait_file="/keyspressing.mp3",
            description="Remove an item from the order",
            parameters={
                "type": "object",
                "properties": {
                    "item_name": {
                        "type": "string",
                        "description": "Name of item to remove"
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "How many to remove (default: 1, use -1 for all)",
                        "minimum": -1
                    }
                },
                "required": ["item_name"]
            }
        )
        def remove_item(args, raw_data):
            """Remove item from order"""
            order_state, global_data = get_order_state(raw_data)
            item_name = args["item_name"]
            quantity_to_remove = args.get("quantity", 1)  # Default to removing 1
            
            # First try to find the item in menu using fuzzy matching to get the proper name
            sku, item_data, category = find_menu_item(item_name)
            
            # Find item in order using either the fuzzy matched name or direct search
            target_item = None
            item_index = None
            
            if sku:
                # Use SKU for exact match if we found it in menu
                for i, order_item in enumerate(order_state["items"]):
                    if order_item["sku"] == sku:
                        target_item = order_item
                        item_index = i
                        break
            
            # If not found by SKU, try fuzzy match on the name in the order
            if not target_item:
                item_lower = item_name.lower()
                # Try exact substring match first
                for i, order_item in enumerate(order_state["items"]):
                    if item_lower in order_item["name"].lower():
                        target_item = order_item
                        item_index = i
                        break
                
                # If still not found, try more flexible matching
                if not target_item:
                    # Check for partial matches (e.g., "bottles" matches "Bottled Water")
                    for i, order_item in enumerate(order_state["items"]):
                        order_name_lower = order_item["name"].lower()
                        # Check if any word in the search matches any word in the item name
                        search_words = item_lower.split()
                        item_words = order_name_lower.split()
                        for search_word in search_words:
                            for item_word in item_words:
                                if search_word in item_word or item_word in search_word:
                                    target_item = order_item
                                    item_index = i
                                    break
                            if target_item:
                                break
                        if target_item:
                            break
            
            if not target_item:
                return SwaigFunctionResult(f"You don't have {item_name} in your order.")
            
            # Handle quantity removal
            item_completely_removed = False
            
            if quantity_to_remove == -1 or quantity_to_remove >= target_item["quantity"]:
                # Remove all
                removed_item = order_state["items"].pop(item_index)
                quantity_removed = removed_item["quantity"]
                item_completely_removed = True
                response = f"Removed all {quantity_removed} {removed_item['name']}{'s' if quantity_removed > 1 else ''} from your order."
            else:
                # Remove partial quantity
                if quantity_to_remove <= 0:
                    quantity_to_remove = 1
                    
                quantity_removed = min(quantity_to_remove, target_item["quantity"])
                target_item["quantity"] -= quantity_removed
                target_item["total"] = round(target_item["price"] * target_item["quantity"], 2)
                
                if target_item["quantity"] == 0:
                    # If we removed all, remove the item
                    removed_item = order_state["items"].pop(item_index)
                    item_completely_removed = True
                else:
                    removed_item = target_item  # Keep reference for event
                    item_completely_removed = False
                
                response = f"Removed {quantity_removed} {target_item['name']}{'s' if quantity_removed > 1 else ''} from your order."
                if not item_completely_removed:
                    response += f" You still have {target_item['quantity']} remaining."
            
            # Recalculate totals
            order_state["subtotal"], order_state["tax"], order_state["total"] = calculate_totals(order_state["items"])
            order_state["item_count"] = sum(item["quantity"] for item in order_state["items"])
            
            if order_state["total"] > 0:
                response += f" Your new total is {dollars_to_words(order_state['total'])}."
            else:
                response += " Your order is now empty."
            
            result = SwaigFunctionResult(response)
            save_order_state(result, order_state, global_data)
            
            # Send event to UI based on whether item was completely removed
            if item_completely_removed:
                # Item completely removed
                result.add_action("user_event", {
                    "type": "item_removed",
                    "sku": removed_item["sku"],
                    "order_total": order_state["total"],
                    "subtotal": order_state["subtotal"],
                    "tax": order_state["tax"],
                    "item_count": order_state["item_count"]
                })
            else:
                # Item still exists with reduced quantity
                result.add_action("user_event", {
                    "type": "quantity_modified",
                    "sku": removed_item["sku"],
                    "new_quantity": removed_item["quantity"],
                    "new_total": removed_item["total"],
                    "order_total": order_state["total"],
                    "subtotal": order_state["subtotal"],
                    "tax": order_state["tax"],
                    "item_count": order_state["item_count"]
                })
            
            return result
        
        # (Continuing with the rest of the tools as in the original...)
        # I'll just add the skeletons to keep the file complete
        
        @self.tool(
            name="modify_quantity",
            wait_file="/keyspressing.mp3",
            description="Change the quantity of an item already in the order",
            parameters={
                "type": "object",
                "properties": {
                    "item_name": {
                        "type": "string",
                        "description": "Name of the item to modify"
                    },
                    "new_quantity": {
                        "type": "integer",
                        "description": "New quantity (0 to remove)",
                        "minimum": 0,
                        "maximum": 10
                    }
                },
                "required": ["item_name", "new_quantity"]
            }
        )
        def modify_quantity(args, raw_data):
            """Modify quantity of an existing item"""
            order_state, global_data = get_order_state(raw_data)
            item_name = args["item_name"]
            new_quantity = args["new_quantity"]
            
            # Same limits as add_item
            MAX_ITEMS_PER_TYPE = 20
            MAX_TOTAL_ITEMS = 50
            MAX_ORDER_VALUE = 500.00
            
            # Find item
            item_lower = item_name.lower()
            modified_item = None
            
            for order_item in order_state["items"]:
                if item_lower in order_item["name"].lower():
                    if new_quantity == 0:
                        # Remove item
                        order_state["items"].remove(order_item)
                        response = f"Removed {order_item['name']} from your order."
                    else:
                        # Validate new quantity
                        if new_quantity > MAX_ITEMS_PER_TYPE:
                            new_quantity = MAX_ITEMS_PER_TYPE
                            response = f"Changed {order_item['name']} quantity to {new_quantity} (maximum per item type)."
                        else:
                            response = f"Changed {order_item['name']} quantity to {new_quantity}."
                        
                        # Check total items limit
                        current_total = sum(item["quantity"] for item in order_state["items"]) - order_item["quantity"]
                        if current_total + new_quantity > MAX_TOTAL_ITEMS:
                            new_quantity = MAX_TOTAL_ITEMS - current_total
                            response = f"Changed {order_item['name']} quantity to {new_quantity} (to stay within {MAX_TOTAL_ITEMS} item limit)."
                        
                        # Check value limit
                        potential_subtotal = sum(item["total"] for item in order_state["items"]) - order_item["total"] + (order_item["price"] * new_quantity)
                        if potential_subtotal > MAX_ORDER_VALUE:
                            max_quantity_by_value = int((MAX_ORDER_VALUE - (sum(item["total"] for item in order_state["items"]) - order_item["total"])) / order_item["price"])
                            new_quantity = max_quantity_by_value
                            response = f"Changed {order_item['name']} quantity to {new_quantity} (to stay within ${MAX_ORDER_VALUE} order limit)."
                        
                        # Update quantity
                        order_item["quantity"] = new_quantity
                        order_item["total"] = round(order_item["price"] * new_quantity, 2)
                    modified_item = order_item
                    break
            
            if not modified_item:
                return SwaigFunctionResult(f"You don't have {item_name} in your order.")
            
            # Recalculate totals
            order_state["subtotal"], order_state["tax"], order_state["total"] = calculate_totals(order_state["items"])
            order_state["item_count"] = sum(item["quantity"] for item in order_state["items"])
            
            if order_state["total"] > 0:
                response += f" Your new total is {dollars_to_words(order_state['total'])}."
            
            result = SwaigFunctionResult(response)
            save_order_state(result, order_state, global_data)
            
            # Send event to UI
            result.add_action("user_event", {
                "type": "quantity_modified",
                "sku": modified_item.get("sku"),
                "new_quantity": new_quantity if new_quantity > 0 else 0,
                "new_total": modified_item["total"] if modified_item and new_quantity > 0 else 0,
                "order_total": order_state["total"],
                "subtotal": order_state["subtotal"],
                "tax": order_state["tax"],
                "item_count": order_state["item_count"]
            })
            
            return result
        
        @self.tool(
            name="review_order",
            wait_file="/keyspressing.mp3",
            description="Review the current order",
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
        def review_order(args, raw_data):
            """Display current order with totals"""
            order_state, global_data = get_order_state(raw_data)
            
            if not order_state["items"]:
                return SwaigFunctionResult("Your order is empty. What would you like to order?")
            
            # Just give the total - they can see the details on screen
            response = f"Your current total is {dollars_to_words(order_state['total'])}. You can see your order on the screen."
            
            result = SwaigFunctionResult(response)
            
            # Send complete order to UI
            result.add_action("user_event", {
                "type": "order_reviewed",
                "items": order_state["items"],
                "subtotal": order_state["subtotal"],
                "tax": order_state["tax"],
                "total": order_state["total"]
            })
            
            return result
        
        @self.tool(
            name="finalize_order",
            wait_file="/keyspressing.mp3",
            description="Finalize order and move to confirmation",
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
        def finalize_order(args, raw_data):
            """Move to order confirmation"""
            order_state, global_data = get_order_state(raw_data)
            
            if not order_state["items"]:
                return SwaigFunctionResult("Your order is empty. Please add some items first!")
            
            # Simple confirmation - they can see the order on screen
            response = f"Alright, your total comes to {dollars_to_words(order_state['total'])}. Does everything on the screen look correct?"
            
            result = SwaigFunctionResult(response)
            save_order_state(result, order_state, global_data)
            
            # Move to confirming state
            result.swml_change_step("confirming_order")
            
            # Send event to UI with complete order details
            result.add_action("user_event", {
                "type": "order_finalized",
                "items": order_state["items"],
                "subtotal": order_state["subtotal"],
                "tax": order_state["tax"],
                "total": order_state["total"],
                "item_count": order_state["item_count"]
            })
            
            return result
        
        @self.tool(
            name="process_payment",
            wait_file="/keyspressing.mp3",
            description="Process the payment",
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
        def process_payment(args, raw_data):
            """Process payment and generate order number"""
            order_state, global_data = get_order_state(raw_data)
            
            # Generate order number
            order_state["order_number"] = random.randint(100, 999)
            
            response = f"Perfect! Your order number is {order_number_to_words(order_state['order_number'])}.\n"
            response += f"Your total is {dollars_to_words(order_state['total'])}.\n\n"
            response += "Please pull forward to the first window to pay."
            
            result = SwaigFunctionResult(response)
            save_order_state(result, order_state, global_data)
            
            # Move to payment processing
            result.swml_change_step("payment_processing")
            
            # Send event to UI
            result.add_action("user_event", {
                "type": "payment_started",
                "order_number": order_state["order_number"],
                "total": order_state["total"]
            })
            
            return result
        
        @self.tool(
            name="complete_order",
            wait_file="/keyspressing.mp3",
            description="Complete the order",
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
        def complete_order(args, raw_data):
            """Mark order as complete"""
            order_state, global_data = get_order_state(raw_data)
            
            order_number = order_state['order_number']
            
            response = f"Thank you for your order! Order number {order_number_to_words(order_number)} is complete.\n"
            response += "Have a wonderful day!"
            
            # Clear the order but keep the order number
            order_state["items"] = []
            order_state["total"] = 0.00
            order_state["subtotal"] = 0.00
            order_state["tax"] = 0.00
            order_state["item_count"] = 0
            # Keep order_number to display it
            
            result = SwaigFunctionResult(response)
            save_order_state(result, order_state, global_data)
            
            # Move to complete state
            result.swml_change_step("order_complete")
            
            # Send event to UI
            result.add_action("user_event", {
                "type": "order_completed",
                "order_number": order_number
            })
            
            return result
        
        @self.tool(
            name="cancel_order",
            wait_file="/keyspressing.mp3",
            description="Clear/cancel the entire order. Use when customer says 'cancel', 'start over', 'never mind', or before adding a single item when they say 'I only want X'",
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
        def cancel_order(args, raw_data):
            """Cancel and reset order"""
            order_state, global_data = get_order_state(raw_data)
            
            # Reset order
            order_state["items"] = []
            order_state["total"] = 0.00
            order_state["subtotal"] = 0.00
            order_state["tax"] = 0.00
            order_state["order_number"] = None
            order_state["item_count"] = 0
            
            # Check current state to determine response
            current_step = global_data.get("current_step", "greeting")
            if current_step == "taking_order":
                # Stay in taking_order for "never mind, I just want X" scenarios
                response = "Alright, I've cleared everything. What would you like?"
                result = SwaigFunctionResult(response)
                save_order_state(result, order_state, global_data)
                # Stay in taking_order state
                result.context = "taking_order"
            else:
                # From confirming state or elsewhere, go back to greeting
                response = "Order cancelled. How can I help you today?"
                result = SwaigFunctionResult(response)
                save_order_state(result, order_state, global_data)
                # Go back to greeting
                result.context = "greeting"
            
            # Send event to UI
            result.add_action("user_event", {
                "type": "order_cancelled",
                "items": [],
                "subtotal": 0,
                "tax": 0,
                "total": 0
            })
            
            return result
        
        @self.tool(
            name="new_order",
            wait_file="/keyspressing.mp3",
            description="Start a new order",
            parameters={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
        def new_order(args, raw_data):
            """Start fresh order"""
            order_state, global_data = get_order_state(raw_data)
            
            # Reset order
            order_state["items"] = []
            order_state["total"] = 0.00
            order_state["subtotal"] = 0.00
            order_state["tax"] = 0.00
            order_state["order_number"] = None
            order_state["item_count"] = 0
            
            response = "Welcome back to Holy Guacamole! What can I get started for you?"
            
            result = SwaigFunctionResult(response)
            save_order_state(result, order_state, global_data)
            
            # Go to greeting
            result.swml_change_step("greeting")
            
            # Send event to UI
            result.add_action("user_event", {
                "type": "new_order"
            })
            
            return result
        
        @self.tool(
            name="upgrade_to_combo",
            wait_file="/keyspressing.mp3",
            description="Upgrade individual items to a combo meal",
            parameters={
                "type": "object",
                "properties": {
                    "combo_type": {
                        "type": "string",
                        "description": "Type of combo: 'taco', 'burrito', or 'both'"
                    }
                },
                "required": ["combo_type"]
            }
        )
        def upgrade_to_combo(args, raw_data):
            """Replace individual items with a combo meal to save money"""
            order_state, global_data = get_order_state(raw_data)
            combo_type = args["combo_type"].lower()
            
            # Handle "both" by upgrading both combos
            if combo_type == "both":
                # We'll process both taco and burrito combos
                combos_to_add = []
                removed_items = []
                items_to_keep = []
                
                # First pass: count what we have
                taco_count = sum(item["quantity"] for item in order_state["items"] if "taco" in item["name"].lower())
                burrito_count = sum(item["quantity"] for item in order_state["items"] if "burrito" in item["name"].lower())
                chips_count = sum(item["quantity"] for item in order_state["items"] if "chips" in item["name"].lower() and "salsa" in item["name"].lower())
                drink_count = sum(item["quantity"] for item in order_state["items"] if "small" in item["name"].lower() and "drink" in item["name"].lower())
                
                # Calculate how many of each combo we can make
                max_taco_combos = min(taco_count // 2, chips_count, drink_count)
                # After taco combos, recalculate remaining items for burrito combos
                remaining_chips = chips_count - max_taco_combos
                remaining_drinks = drink_count - max_taco_combos
                max_burrito_combos = min(burrito_count, remaining_chips, remaining_drinks)
                
                # Track what we need to remove
                tacos_to_remove = max_taco_combos * 2
                burritos_to_remove = max_burrito_combos
                chips_to_remove = max_taco_combos + max_burrito_combos
                drinks_to_remove = max_taco_combos + max_burrito_combos
                
                # Process each item
                for item in order_state["items"]:
                    item_lower = item["name"].lower()
                    item_to_keep = item.copy()
                    
                    if "taco" in item_lower and tacos_to_remove > 0:
                        if item["quantity"] <= tacos_to_remove:
                            removed_items.append(item)
                            tacos_to_remove -= item["quantity"]
                            continue
                        else:
                            removed_item = item.copy()
                            removed_item["quantity"] = tacos_to_remove
                            removed_item["total"] = round(removed_item["price"] * tacos_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            item_to_keep["quantity"] -= tacos_to_remove
                            item_to_keep["total"] = round(item_to_keep["price"] * item_to_keep["quantity"], 2)
                            tacos_to_remove = 0
                    
                    elif "burrito" in item_lower and burritos_to_remove > 0:
                        if item["quantity"] <= burritos_to_remove:
                            removed_items.append(item)
                            burritos_to_remove -= item["quantity"]
                            continue
                        else:
                            removed_item = item.copy()
                            removed_item["quantity"] = burritos_to_remove
                            removed_item["total"] = round(removed_item["price"] * burritos_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            item_to_keep["quantity"] -= burritos_to_remove
                            item_to_keep["total"] = round(item_to_keep["price"] * item_to_keep["quantity"], 2)
                            burritos_to_remove = 0
                    
                    elif "chips" in item_lower and "salsa" in item_lower and chips_to_remove > 0:
                        if item["quantity"] <= chips_to_remove:
                            removed_items.append(item)
                            chips_to_remove -= item["quantity"]
                            continue
                        else:
                            removed_item = item.copy()
                            removed_item["quantity"] = chips_to_remove
                            removed_item["total"] = round(removed_item["price"] * chips_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            item_to_keep["quantity"] -= chips_to_remove
                            item_to_keep["total"] = round(item_to_keep["price"] * item_to_keep["quantity"], 2)
                            chips_to_remove = 0
                    
                    elif "small" in item_lower and "drink" in item_lower and drinks_to_remove > 0:
                        if item["quantity"] <= drinks_to_remove:
                            removed_items.append(item)
                            drinks_to_remove -= item["quantity"]
                            continue
                        else:
                            removed_item = item.copy()
                            removed_item["quantity"] = drinks_to_remove
                            removed_item["total"] = round(removed_item["price"] * drinks_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            item_to_keep["quantity"] -= drinks_to_remove
                            item_to_keep["total"] = round(item_to_keep["price"] * item_to_keep["quantity"], 2)
                            drinks_to_remove = 0
                    
                    if item_to_keep["quantity"] > 0:
                        items_to_keep.append(item_to_keep)
                
                # Add both combos with proper quantities
                if max_taco_combos > 0:
                    combos_to_add.append({
                        "sku": "C001",
                        "name": "Taco Combo",
                        "description": "2 tacos (your choice) + chips & salsa + small drink",
                        "price": 9.99,
                        "quantity": max_taco_combos,
                        "total": round(9.99 * max_taco_combos, 2)
                    })
                
                if max_burrito_combos > 0:
                    combos_to_add.append({
                        "sku": "C002",
                        "name": "Burrito Combo",
                        "description": "Any burrito + chips & salsa + small drink",
                        "price": 12.99,
                        "quantity": max_burrito_combos,
                        "total": round(12.99 * max_burrito_combos, 2)
                    })
                
                # Calculate total savings
                removed_total = sum(item["total"] for item in removed_items)
                combo_total = sum(combo["total"] for combo in combos_to_add)
                savings = round(removed_total - combo_total, 2)
                
                # Update order
                items_to_keep.extend(combos_to_add)
                order_state["items"] = items_to_keep
                order_state["subtotal"], order_state["tax"], order_state["total"] = calculate_totals(order_state["items"])
                order_state["item_count"] = sum(item["quantity"] for item in order_state["items"])
                
                # Build response
                combo_descriptions = []
                for c in combos_to_add:
                    if c["quantity"] > 1:
                        combo_descriptions.append(f"{c['quantity']} {c['name']}s")
                    else:
                        combo_descriptions.append(f"a {c['name']}")
                combo_names = " and ".join(combo_descriptions)
                response = f"Awesome! I've upgraded your order to {combo_names}, saving you {dollars_to_words(savings)}!"
                response += f" Your new total is {dollars_to_words(order_state['total'])}."
                
                result = SwaigFunctionResult(response)
                save_order_state(result, order_state, global_data)
                
                # Send event
                result.add_action("user_event", {
                    "type": "combo_upgraded",
                    "items": order_state["items"],
                    "removed_items": [{"name": item["name"], "quantity": item["quantity"]} for item in removed_items],
                    "added_combos": combos_to_add,
                    "subtotal": order_state["subtotal"],
                    "tax": order_state["tax"],
                    "total": order_state["total"],
                    "savings": savings,
                    "item_count": order_state["item_count"]
                })
                
                return result
            
            # Original single combo upgrade logic - now handles multiple combos
            removed_items = []
            items_to_keep = []
            
            if combo_type == "taco":
                # Calculate how many taco combos we can make
                # Need: 2 tacos, 1 chips & salsa, 1 small drink per combo
                taco_count = sum(item["quantity"] for item in order_state["items"] if "taco" in item["name"].lower())
                chips_count = sum(item["quantity"] for item in order_state["items"] if "chips" in item["name"].lower() and "salsa" in item["name"].lower())
                drink_count = sum(item["quantity"] for item in order_state["items"] if "small" in item["name"].lower() and "drink" in item["name"].lower())
                
                # Maximum combos we can make
                max_combos = min(taco_count // 2, chips_count, drink_count)
                
                if max_combos <= 0:
                    return SwaigFunctionResult("You don't have enough items for a Taco Combo. You need 2 tacos, chips & salsa, and a small drink.")
                
                # Track how many of each item we need to remove
                tacos_to_remove = max_combos * 2
                chips_to_remove = max_combos
                drinks_to_remove = max_combos
                
                # Process each item
                for item in order_state["items"]:
                    item_lower = item["name"].lower()
                    
                    # Remove tacos
                    if "taco" in item_lower and tacos_to_remove > 0:
                        if item["quantity"] <= tacos_to_remove:
                            removed_items.append(item)
                            tacos_to_remove -= item["quantity"]
                        else:
                            # Remove only what we need, keep the rest
                            removed_item = item.copy()
                            removed_item["quantity"] = tacos_to_remove
                            removed_item["total"] = round(removed_item["price"] * tacos_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            # Keep the remaining tacos
                            remaining = item.copy()
                            remaining["quantity"] = item["quantity"] - tacos_to_remove
                            remaining["total"] = round(remaining["price"] * remaining["quantity"], 2)
                            items_to_keep.append(remaining)
                            tacos_to_remove = 0
                    # Remove chips & salsa
                    elif "chips" in item_lower and "salsa" in item_lower and chips_to_remove > 0:
                        if item["quantity"] <= chips_to_remove:
                            removed_items.append(item)
                            chips_to_remove -= item["quantity"]
                        else:
                            removed_item = item.copy()
                            removed_item["quantity"] = chips_to_remove
                            removed_item["total"] = round(removed_item["price"] * chips_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            remaining = item.copy()
                            remaining["quantity"] = item["quantity"] - chips_to_remove
                            remaining["total"] = round(remaining["price"] * remaining["quantity"], 2)
                            items_to_keep.append(remaining)
                            chips_to_remove = 0
                    # Remove small drinks
                    elif "small" in item_lower and "drink" in item_lower and drinks_to_remove > 0:
                        if item["quantity"] <= drinks_to_remove:
                            removed_items.append(item)
                            drinks_to_remove -= item["quantity"]
                        else:
                            removed_item = item.copy()
                            removed_item["quantity"] = drinks_to_remove
                            removed_item["total"] = round(removed_item["price"] * drinks_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            remaining = item.copy()
                            remaining["quantity"] = item["quantity"] - drinks_to_remove
                            remaining["total"] = round(remaining["price"] * remaining["quantity"], 2)
                            items_to_keep.append(remaining)
                            drinks_to_remove = 0
                    else:
                        items_to_keep.append(item)
                
                # Add Taco Combo(s)
                combo = {
                    "sku": "C001",
                    "name": "Taco Combo",
                    "description": "2 tacos (your choice) + chips & salsa + small drink",
                    "price": 9.99,
                    "quantity": max_combos,
                    "total": round(9.99 * max_combos, 2)
                }
                
            elif combo_type == "burrito":
                # Calculate how many burrito combos we can make
                # Need: 1 burrito, 1 chips & salsa, 1 small drink per combo
                burrito_count = sum(item["quantity"] for item in order_state["items"] if "burrito" in item["name"].lower())
                chips_count = sum(item["quantity"] for item in order_state["items"] if "chips" in item["name"].lower() and "salsa" in item["name"].lower())
                drink_count = sum(item["quantity"] for item in order_state["items"] if "small" in item["name"].lower() and "drink" in item["name"].lower())
                
                # Maximum combos we can make
                max_combos = min(burrito_count, chips_count, drink_count)
                
                if max_combos <= 0:
                    return SwaigFunctionResult("You don't have enough items for a Burrito Combo. You need a burrito, chips & salsa, and a small drink.")
                
                # Track how many of each item we need to remove
                burritos_to_remove = max_combos
                chips_to_remove = max_combos
                drinks_to_remove = max_combos
                
                # Process each item
                for item in order_state["items"]:
                    item_lower = item["name"].lower()
                    
                    # Remove burritos
                    if "burrito" in item_lower and burritos_to_remove > 0:
                        if item["quantity"] <= burritos_to_remove:
                            removed_items.append(item)
                            burritos_to_remove -= item["quantity"]
                        else:
                            # Remove only what we need, keep the rest
                            removed_item = item.copy()
                            removed_item["quantity"] = burritos_to_remove
                            removed_item["total"] = round(removed_item["price"] * burritos_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            # Keep the remaining burritos
                            remaining = item.copy()
                            remaining["quantity"] = item["quantity"] - burritos_to_remove
                            remaining["total"] = round(remaining["price"] * remaining["quantity"], 2)
                            items_to_keep.append(remaining)
                            burritos_to_remove = 0
                    # Remove chips & salsa
                    elif "chips" in item_lower and "salsa" in item_lower and chips_to_remove > 0:
                        if item["quantity"] <= chips_to_remove:
                            removed_items.append(item)
                            chips_to_remove -= item["quantity"]
                        else:
                            removed_item = item.copy()
                            removed_item["quantity"] = chips_to_remove
                            removed_item["total"] = round(removed_item["price"] * chips_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            remaining = item.copy()
                            remaining["quantity"] = item["quantity"] - chips_to_remove
                            remaining["total"] = round(remaining["price"] * remaining["quantity"], 2)
                            items_to_keep.append(remaining)
                            chips_to_remove = 0
                    # Remove small drinks
                    elif "small" in item_lower and "drink" in item_lower and drinks_to_remove > 0:
                        if item["quantity"] <= drinks_to_remove:
                            removed_items.append(item)
                            drinks_to_remove -= item["quantity"]
                        else:
                            removed_item = item.copy()
                            removed_item["quantity"] = drinks_to_remove
                            removed_item["total"] = round(removed_item["price"] * drinks_to_remove, 2)
                            removed_items.append(removed_item)
                            
                            remaining = item.copy()
                            remaining["quantity"] = item["quantity"] - drinks_to_remove
                            remaining["total"] = round(remaining["price"] * remaining["quantity"], 2)
                            items_to_keep.append(remaining)
                            drinks_to_remove = 0
                    else:
                        items_to_keep.append(item)
                
                # Add Burrito Combo(s)
                combo = {
                    "sku": "C002",
                    "name": "Burrito Combo",
                    "description": "Any burrito + chips & salsa + small drink",
                    "price": 12.99,
                    "quantity": max_combos,
                    "total": round(12.99 * max_combos, 2)
                }
            else:
                return SwaigFunctionResult("I can only upgrade to taco or burrito combos.")
            
            # Calculate savings
            removed_total = sum(item["total"] for item in removed_items)
            savings = removed_total - combo["total"]
            
            # Update order
            items_to_keep.append(combo)
            order_state["items"] = items_to_keep
            order_state["subtotal"], order_state["tax"], order_state["total"] = calculate_totals(order_state["items"])
            order_state["item_count"] = sum(item["quantity"] for item in order_state["items"])
            
            # Build response
            if combo["quantity"] > 1:
                combo_text = f"{combo['quantity']} {combo['name']}s"
            else:
                combo_text = f"the {combo['name']}"
            
            if savings > 0:
                response = f"Great choice! I've upgraded your order to {combo_text} and saved you {dollars_to_words(savings)}!"
            else:
                response = f"I've upgraded your order to {combo_text}."
            response += f" Your new total is {dollars_to_words(order_state['total'])}."
            
            result = SwaigFunctionResult(response)
            save_order_state(result, order_state, global_data)
            
            # Send comprehensive event to UI
            result.add_action("user_event", {
                "type": "combo_upgraded",
                "items": order_state["items"],
                "removed_items": [{"name": item["name"], "quantity": item["quantity"]} for item in removed_items],
                "added_combo": combo,
                "subtotal": order_state["subtotal"],
                "tax": order_state["tax"],
                "total": order_state["total"],
                "savings": savings,
                "item_count": order_state["item_count"]
            })
            
            return result
        
        # Configure voice
        self.add_language(
            name="English",
            code="en-US",
            voice="elevenlabs.adam"
        )
        
        # Add speech hints
        self.add_hints([
            "taco", "burrito", "quesadilla",
            "beef", "chicken", "bean", "cheese",
            "chips", "salsa", "guacamole",
            "drink", "water", "combo",
            "small", "large",
            "yes", "no", "done", "finished",
            "add", "remove", "cancel"
        ])
        
        # Set conversation parameters (video URLs will be set dynamically)
        self.set_param("turn_detection_timeout", "300")
        self.set_param("end_of_speech_timeout", "2000")

        self.set_prompt_llm_params(
            temperature=0.1,
            top_p=0.1
        )

        # Optional post-prompt URL from environment
        post_prompt_url = os.environ.get("GUACAMOLE_POST_PROMPT_URL")
        if post_prompt_url:
            self.set_post_prompt("Summarize the conversation, including all the details about the food order and any special requests.")
            self.set_post_prompt_url(post_prompt_url)

        # Initialize global data
        self.set_global_data({
            "restaurant": "Holy Guacamole!",
            "order_state": {
                "items": [],
                "total": 0.00,
                "subtotal": 0.00,
                "tax": 0.00,
                "order_number": None,
                "item_count": 0
            }
        })
    
    def on_swml_request(self, request_data=None, callback_path=None, request=None):
        """Override to dynamically set video URLs based on request origin"""
        # Get the host from the request object if available
        host = None
        
        if request:
            # Try to get host from the Starlette request headers
            headers = dict(request.headers)
            host = headers.get('host') or headers.get('x-forwarded-host')
            
            # Check if we're behind a proxy with x-forwarded-proto
            protocol = headers.get('x-forwarded-proto', 'https')
            
            # Override protocol for local development
            if host and ('localhost' in host or '127.0.0.1' in host):
                protocol = 'http'
        
        # If we found a host, update the video URLs
        if host:
            base_url = f"{protocol}://{host}"
            # Use set_param to set individual params instead of set_params to avoid clobbering
            self.set_param("video_idle_file", f"{base_url}/sigmond_cc_idle.mp4")
            self.set_param("video_talking_file", f"{base_url}/sigmond_cc_talking.mp4")
            print(f"Set video URLs to use host: {base_url}")
        else:
            # Fallback to default if no host header found
            self.set_param("video_idle_file", "https://briankwest.ngrok.io/sigmond_cc_idle.mp4")
            self.set_param("video_talking_file", "https://briankwest.ngrok.io/sigmond_cc_talking.mp4")
            print("No host header found, using default video URLs")
        
        # Call parent implementation
        return super().on_swml_request(request_data, callback_path, request)
    
    def _initialize_tfidf(self):
        """Initialize TF-IDF vectorizer with menu items"""
        corpus = []
        self.sku_map = []
        
        # Build corpus from menu items
        for category, items in MENU.items():
            for sku, item in items.items():
                # Combine name, description, and aliases for better matching
                text_parts = [item['name']]
                
                # Add description if available
                if 'description' in item:
                    text_parts.append(item['description'])
                
                # Add aliases if available
                if sku in MENU_ALIASES:
                    text_parts.extend(MENU_ALIASES[sku])
                
                # Add category name for context
                text_parts.append(category)
                
                # Create combined text
                text = " ".join(text_parts).lower()
                corpus.append(text)
                self.sku_map.append((sku, item, category))
        
        # Create and fit vectorizer
        self.vectorizer = TfidfVectorizer(
            ngram_range=(1, 2),  # Use unigrams and bigrams
            stop_words=None,  # Keep all words for menu matching
            max_features=200,
            sublinear_tf=True
        )
        self.menu_vectors = self.vectorizer.fit_transform(corpus)
    


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
