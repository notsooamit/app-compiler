"""
Evaluation dataset: 10 real product prompts + 10 edge cases.
Each prompt has metadata for categorization and expected difficulty.
"""

REAL_PROMPTS = [
    {
        "id": "real_01",
        "category": "real",
        "prompt": "Build a simple todo app where users can add tasks, mark them complete, and delete them. Tasks should have a title, due date, and priority (low/medium/high).",
        "expected_complexity": "simple",
        "expected_entities": ["tasks"],
        "description": "Simple todo app",
    },
    {
        "id": "real_02",
        "category": "real",
        "prompt": "Create a blog platform. Authors can write posts with titles, content, tags, and publish date. Readers can comment on posts. Comments need author name and text. Posts should be filterable by tag.",
        "expected_complexity": "medium",
        "expected_entities": ["posts", "comments", "tags", "users"],
        "description": "Blog with comments",
    },
    {
        "id": "real_03",
        "category": "real",
        "prompt": "Build an e-commerce store with products (name, price, description, image, stock), shopping cart, orders (status, total, shipping address), and users. Users can browse products, add to cart, and place orders. Admin can manage inventory.",
        "expected_complexity": "medium",
        "expected_entities": ["products", "orders", "users", "cart_items"],
        "description": "E-commerce store",
    },
    {
        "id": "real_04",
        "category": "real",
        "prompt": "Build a CRM with login, contacts (name, email, phone, company), deals (value, stage, contact), dashboard showing pipeline value, role-based access (admin can see everything, sales rep only their own deals), and a premium plan with advanced analytics.",
        "expected_complexity": "complex",
        "expected_entities": ["contacts", "deals", "users", "companies"],
        "description": "CRM with contacts & deals",
    },
    {
        "id": "real_05",
        "category": "real",
        "prompt": "Project management tool: users create projects, add team members, create tasks within projects (assignee, status: todo/in-progress/done, priority, due date). Dashboard shows project progress. Users get notifications for task assignments.",
        "expected_complexity": "medium",
        "expected_entities": ["projects", "tasks", "users", "notifications"],
        "description": "Project management tool",
    },
    {
        "id": "real_06",
        "category": "real",
        "prompt": "Social media feed app: users can create posts with text and images, follow other users, like posts, and comment. Feed shows posts from followed users sorted by recency. Hashtags on posts for discovery.",
        "expected_complexity": "medium",
        "expected_entities": ["posts", "users", "comments", "likes", "follows"],
        "description": "Social media feed",
    },
    {
        "id": "real_07",
        "category": "real",
        "prompt": "Job board: employers post jobs (title, description, salary, location, requirements). Job seekers create profiles with resume and skills. They can search jobs by keyword/location and apply. Employers can review applications and change status (reviewing, shortlisted, rejected, hired).",
        "expected_complexity": "medium",
        "expected_entities": ["jobs", "users", "applications", "profiles"],
        "description": "Job board with applications",
    },
    {
        "id": "real_08",
        "category": "real",
        "prompt": "Event booking system: organizers create events (name, date, venue, capacity, ticket price). Users browse events, book tickets, and receive email confirmations. Each booking has a unique QR code. Organizers can scan QR codes for check-in. Events can be free or paid.",
        "expected_complexity": "medium",
        "expected_entities": ["events", "bookings", "users", "tickets"],
        "description": "Event booking system",
    },
    {
        "id": "real_09",
        "category": "real",
        "prompt": "Learning management system: instructors create courses with modules, lessons (video/text), and quizzes. Students enroll in courses, track progress, take quizzes, and earn certificates on completion. Admin dashboard shows enrollment stats and revenue.",
        "expected_complexity": "complex",
        "expected_entities": ["courses", "modules", "lessons", "quizzes", "users", "enrollments", "certificates"],
        "description": "Learning management system",
    },
    {
        "id": "real_10",
        "category": "real",
        "prompt": "SaaS subscription platform with three plans (basic, pro, enterprise). Users sign up with email/password, pick a plan, add payment method (Stripe integration). Features are gated by plan level. Admin dashboard shows MRR, churn rate, active users. Team members can be invited (enterprise only). API access is available for pro and enterprise.",
        "expected_complexity": "complex",
        "expected_entities": ["users", "subscriptions", "payments", "teams", "plans", "api_keys"],
        "description": "SaaS subscription platform",
    },
]

EDGE_CASES = [
    {
        "id": "edge_01",
        "category": "edge",
        "subcategory": "vague",
        "prompt": "Build an app",
        "expected_complexity": "simple",
        "expected_behavior": "needs_clarification",
        "description": "Ultra-minimal prompt",
    },
    {
        "id": "edge_02",
        "category": "edge",
        "subcategory": "vague",
        "prompt": "Make something cool for my business",
        "expected_complexity": "simple",
        "expected_behavior": "needs_clarification",
        "description": "No specifics at all",
    },
    {
        "id": "edge_03",
        "category": "edge",
        "subcategory": "conflicting",
        "prompt": "Build a public blog where all posts are private and only visible to the author, but anyone can read them without logging in.",
        "expected_complexity": "medium",
        "expected_behavior": "flags_conflict",
        "description": "Conflicting: public vs private",
    },
    {
        "id": "edge_04",
        "category": "edge",
        "subcategory": "conflicting",
        "prompt": "Build a free app where users must pay a $10 subscription fee to create an account.",
        "expected_complexity": "simple",
        "expected_behavior": "flags_conflict",
        "description": "Conflicting: free vs paid",
    },
    {
        "id": "edge_05",
        "category": "edge",
        "subcategory": "incomplete",
        "prompt": "I need a login system.",
        "expected_complexity": "simple",
        "expected_behavior": "needs_clarification_or_assumes",
        "description": "Incomplete: only login",
    },
    {
        "id": "edge_06",
        "category": "edge",
        "subcategory": "incomplete",
        "prompt": "Build a dashboard with charts and analytics.",
        "expected_complexity": "simple",
        "expected_behavior": "needs_clarification_or_assumes",
        "description": "Incomplete: dashboard with no data source",
    },
    {
        "id": "edge_07",
        "category": "edge",
        "subcategory": "scale",
        "prompt": "Build an app with 50 different user roles, each with distinct permissions.",
        "expected_complexity": "complex",
        "expected_behavior": "handles_scale",
        "description": "Over-specified: 50 roles",
    },
    {
        "id": "edge_08",
        "category": "edge",
        "subcategory": "contradictory",
        "prompt": "Build an app with no login requirement where each user has private, secure data that only they can access.",
        "expected_complexity": "medium",
        "expected_behavior": "flags_contradiction",
        "description": "Contradictory: no auth but private data",
    },
    {
        "id": "edge_09",
        "category": "edge",
        "subcategory": "domain_jargon",
        "prompt": "Build a DEX with AMM pools, yield farming, and liquidity provider tokens.",
        "expected_complexity": "complex",
        "expected_behavior": "handles_jargon",
        "description": "Domain-specific jargon (DeFi)",
    },
    {
        "id": "edge_10",
        "category": "edge",
        "subcategory": "nonsense",
        "prompt": "asdfghjkl",
        "expected_complexity": "simple",
        "expected_behavior": "needs_clarification",
        "description": "Nonsense input",
    },
]

ALL_PROMPTS = REAL_PROMPTS + EDGE_CASES


def get_real_prompts():
    return REAL_PROMPTS


def get_edge_cases():
    return EDGE_CASES


def get_all_prompts():
    return ALL_PROMPTS
