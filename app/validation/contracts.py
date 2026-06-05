"""
JSON Schema contracts for every stage of the pipeline.
These define the strict output format that each stage must produce.
"""

# ============================================================
# Stage 1 Output: Intent IR
# ============================================================
INTENT_IR_SCHEMA = {
    "type": "object",
    "required": ["app_name", "description", "features", "entities", "roles", "ambiguities"],
    "properties": {
        "app_name": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "features": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "description", "priority"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]}
                }
            }
        },
        "entities": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "description"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "suggested_fields": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                }
            }
        },
        "roles": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "description"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "suggested_permissions": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                }
            }
        },
        "constraints": {
            "type": "array",
            "items": {"type": "string"}
        },
        "ambiguities": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["question", "severity"],
                "properties": {
                    "question": {"type": "string"},
                    "context": {"type": "string"},
                    "severity": {"type": "string", "enum": ["blocking", "warning", "info"]},
                    "assumed_answer": {"type": ["string", "null"]}
                }
            }
        },
        "complexity": {"type": "string", "enum": ["simple", "medium", "complex"]},
        "is_vague": {"type": "boolean"},
        "is_incomplete": {"type": "boolean"},
        "has_conflicts": {"type": "boolean"},
        "clarification_questions": {
            "type": "array",
            "items": {"type": "string"}
        }
    }
}

# ============================================================
# Stage 2 Output: Architecture IR
# ============================================================
ARCHITECTURE_IR_SCHEMA = {
    "type": "object",
    "required": ["app_name", "pages", "entities", "api_endpoints", "auth", "business_rules"],
    "properties": {
        "app_name": {"type": "string"},
        "pages": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "route", "components"],
                "properties": {
                    "name": {"type": "string"},
                    "route": {"type": "string"},
                    "description": {"type": "string"},
                    "components": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "access_roles": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "data_bindings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["component", "entity", "api_endpoint"],
                            "properties": {
                                "component": {"type": "string"},
                                "entity": {"type": "string"},
                                "api_endpoint": {"type": "string"}
                            }
                        }
                    }
                }
            }
        },
        "entities": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "fields"],
                "properties": {
                    "name": {"type": "string"},
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["name", "type"],
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string"},
                                "required": {"type": "boolean"},
                                "unique": {"type": "boolean"},
                                "description": {"type": "string"}
                            }
                        }
                    },
                    "relations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["type", "target"],
                            "properties": {
                                "type": {"type": "string", "enum": ["has_many", "belongs_to", "has_one", "many_to_many"]},
                                "target": {"type": "string"}
                            }
                        }
                    }
                }
            }
        },
        "api_endpoints": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["method", "path", "description"],
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                    "path": {"type": "string"},
                    "description": {"type": "string"},
                    "entity": {"type": "string"},
                    "auth_required": {"type": "boolean"},
                    "roles": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "request_body": {"type": "object"},
                    "response_body": {"type": "object"}
                }
            }
        },
        "auth": {
            "type": "object",
            "required": ["roles", "methods"],
            "properties": {
                "roles": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "permissions"],
                        "properties": {
                            "name": {"type": "string"},
                            "permissions": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        }
                    }
                },
                "methods": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["email_password", "oauth", "magic_link", "jwt", "session"]}
                }
            }
        },
        "business_rules": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "condition", "action"],
                "properties": {
                    "name": {"type": "string"},
                    "condition": {"type": "string"},
                    "action": {"type": "string"},
                    "entities_involved": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                }
            }
        },
        "assumptions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["context", "assumption"],
                "properties": {
                    "context": {"type": "string"},
                    "assumption": {"type": "string"}
                }
            }
        }
    }
}

# ============================================================
# Stage 3 Sub-Schemas: UI, API, DB, Auth, Business Logic
# ============================================================

UI_SCHEMA_SCHEMA = {
    "type": "object",
    "required": ["pages", "global_layout"],
    "properties": {
        "pages": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "route", "layout"],
                "properties": {
                    "name": {"type": "string"},
                    "route": {"type": "string"},
                    "description": {"type": "string"},
                    "access_roles": {"type": "array", "items": {"type": "string"}},
                    "layout": {
                        "type": "object",
                        "required": ["type", "sections"],
                        "properties": {
                            "type": {"type": "string"},
                            "sections": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["name", "components"],
                                    "properties": {
                                        "name": {"type": "string"},
                                        "components": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "required": ["type", "props"],
                                                "properties": {
                                                    "type": {"type": "string"},
                                                    "props": {"type": "object"},
                                                    "data_binding": {"type": "string"},
                                                    "api_action": {"type": "string"},
                                                    "children": {"type": "array"}
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        "global_layout": {
            "type": "object",
            "properties": {
                "navigation": {"type": "array"},
                "sidebar": {"type": ["object", "null"]},
                "footer": {"type": ["object", "null"]}
            }
        },
        "theme": {"type": "object"}
    }
}

API_SCHEMA_SCHEMA = {
    "type": "object",
    "required": ["base_url", "endpoints", "middleware"],
    "properties": {
        "base_url": {"type": "string"},
        "endpoints": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["method", "path", "description"],
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                    "path": {"type": "string"},
                    "description": {"type": "string"},
                    "entity": {"type": "string"},
                    "auth_required": {"type": "boolean"},
                    "roles": {"type": "array", "items": {"type": "string"}},
                    "request_schema": {"type": ["object", "null"]},
                    "response_schema": {"type": "object"},
                    "validation_rules": {"type": "object"},
                    "pagination": {"type": "boolean"},
                    "rate_limit": {"type": "string"}
                }
            }
        },
        "middleware": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "type"],
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": ["auth", "logging", "cors", "rate_limit", "validation"]},
                    "config": {"type": "object"}
                }
            }
        }
    }
}

DB_SCHEMA_SCHEMA = {
    "type": "object",
    "required": ["tables", "relations"],
    "properties": {
        "tables": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "columns"],
                "properties": {
                    "name": {"type": "string"},
                    "columns": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["name", "type"],
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string"},
                                "nullable": {"type": "boolean"},
                                "unique": {"type": "boolean"},
                                "primary_key": {"type": "boolean"},
                                "default": {},
                                "foreign_key": {
                                    "type": "object",
                                    "properties": {
                                        "table": {"type": "string"},
                                        "column": {"type": "string"}
                                    }
                                }
                            }
                        }
                    },
                    "indexes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["columns"],
                            "properties": {
                                "name": {"type": "string"},
                                "columns": {"type": "array", "items": {"type": "string"}},
                                "unique": {"type": "boolean"}
                            }
                        }
                    }
                }
            }
        },
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["type", "from_table", "to_table"],
                "properties": {
                    "type": {"type": "string", "enum": ["has_many", "belongs_to", "has_one", "many_to_many"]},
                    "from_table": {"type": "string"},
                    "to_table": {"type": "string"},
                    "foreign_key": {"type": "string"},
                    "junction_table": {"type": "string"}
                }
            }
        }
    }
}

AUTH_SCHEMA_SCHEMA = {
    "type": "object",
    "required": ["roles", "access_matrix"],
    "properties": {
        "roles": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "permissions"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "permissions": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                }
            }
        },
        "access_matrix": {
            "type": "object",
            "description": "Role -> Resource -> Action mapping",
            "additionalProperties": {
                "type": "object",
                "additionalProperties": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["create", "read", "update", "delete", "list"]}
                }
            }
        },
        "auth_methods": {
            "type": "array",
            "items": {"type": "string"}
        },
        "token_config": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["jwt", "session"]},
                "expires_in": {"type": "string"}
            }
        }
    }
}

BUSINESS_LOGIC_SCHEMA = {
    "type": "object",
    "required": ["rules", "workflows"],
    "properties": {
        "rules": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "description", "trigger", "action"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "trigger": {"type": "string"},
                    "condition": {"type": "string"},
                    "action": {"type": "string"},
                    "entities_involved": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                }
            }
        },
        "workflows": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "steps"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["order", "name", "action"],
                            "properties": {
                                "order": {"type": "integer"},
                                "name": {"type": "string"},
                                "action": {"type": "string"},
                                "next_on_success": {"type": ["integer", "null"]},
                                "next_on_failure": {"type": ["integer", "null"]}
                            }
                        }
                    }
                }
            }
        },
        "feature_gates": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["feature", "condition"],
                "properties": {
                    "feature": {"type": "string"},
                    "condition": {"type": "string"},
                    "description": {"type": "string"}
                }
            }
        }
    }
}

# ============================================================
# Stage 3 Output: Complete Config (wraps all 5 schemas)
# ============================================================
COMPLETE_CONFIG_SCHEMA = {
    "type": "object",
    "required": ["metadata", "ui_schema", "api_schema", "db_schema", "auth_schema", "business_logic"],
    "properties": {
        "metadata": {
            "type": "object",
            "required": ["app_name", "generated_at", "version"],
            "properties": {
                "app_name": {"type": "string"},
                "generated_at": {"type": "string"},
                "version": {"type": "string"},
                "complexity": {"type": "string"}
            }
        },
        "ui_schema": UI_SCHEMA_SCHEMA,
        "api_schema": API_SCHEMA_SCHEMA,
        "db_schema": DB_SCHEMA_SCHEMA,
        "auth_schema": AUTH_SCHEMA_SCHEMA,
        "business_logic": BUSINESS_LOGIC_SCHEMA,
        "assumptions_log": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["stage", "assumption"],
                "properties": {
                    "stage": {"type": "string"},
                    "context": {"type": "string"},
                    "assumption": {"type": "string"},
                    "impact": {"type": "string"}
                }
            }
        },
        "repair_log": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["error", "strategy", "result"],
                "properties": {
                    "error": {"type": "string"},
                    "layer": {"type": "string"},
                    "strategy": {"type": "string"},
                    "result": {"type": "string", "enum": ["fixed", "unresolvable", "partial"]},
                    "detail": {"type": "string"}
                }
            }
        }
    }
}

# Map stage names to their schemas
STAGE_SCHEMAS = {
    "intent": INTENT_IR_SCHEMA,
    "architecture": ARCHITECTURE_IR_SCHEMA,
    "config": COMPLETE_CONFIG_SCHEMA,
}

# Map sub-schema names for Stage 3 parallel generation
SUB_SCHEMAS = {
    "ui": ("ui_schema", UI_SCHEMA_SCHEMA),
    "api": ("api_schema", API_SCHEMA_SCHEMA),
    "db": ("db_schema", DB_SCHEMA_SCHEMA),
    "auth": ("auth_schema", AUTH_SCHEMA_SCHEMA),
    "business_logic": ("business_logic", BUSINESS_LOGIC_SCHEMA),
}
