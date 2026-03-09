# Function (Tool) API Examples

This document provides examples for creating various types of functions (tools) using the Public API.

## 1. Simple GET Request

A basic GET request to a public API endpoint.

**Request Body (POST /api/v2/public/functions):**
```json
{
  "name": "get_current_time",
  "description": "Fetches the current time from a public API",
  "api_config": {
    "url": "https://worldtimeapi.org/api/timezone/Etc/UTC",
    "method": "GET"
  }
}
```

---

## 2. GET with Custom Headers

Useful for APIs requiring simple API Key authentication in headers.

**Request Body:**
```json
{
  "name": "get_weather",
  "description": "Fetches weather data using an API key in headers",
  "api_config": {
    "url": "https://api.weatherapi.com/v1/current.json?q=London",
    "method": "GET",
    "request_headers": {
      "X-API-Key": "your_api_key_here"
    }
  }
}
```

---

## 3. GET with Path Parameters

Placeholders in the URL are defined in `path_params_schema`.

**Request Body:**
```json
{
  "name": "get_user_by_id",
  "description": "Fetches user details by ID",
  "api_config": {
    "url": "https://api.example.com/users/{user_id}",
    "method": "GET",
    "path_params_schema": {
      "user_id": {
        "type": "integer",
        "description": "The unique ID of the user"
      }
    }
  }
}
```

---

## 4. GET with Query Parameters

Parameters appended to the URL as `?key=value`.

**Request Body:**
```json
{
  "name": "search_products",
  "description": "Searches for products with a query and limit",
  "api_config": {
    "url": "https://api.example.com/products/search",
    "method": "GET",
    "query_params_schema": {
      "properties": {
        "query": { "type": "string", "description": "Search term" },
        "limit": { "type": "integer", "description": "Max results" }
      },
      "required": ["query"]
    }
  }
}
```

---

## 5. DELETE Request

Deleting a resource via API.

**Request Body:**
```json
{
  "name": "delete_task",
  "description": "Deletes a task by ID",
  "api_config": {
    "url": "https://api.example.com/tasks/{task_id}",
    "method": "DELETE",
    "path_params_schema": {
      "task_id": { "type": "string" }
    }
  }
}
```

---

## 6. POST with Simple Body (Flat Object)

A standard POST request with a JSON body.

**Request Body:**
```json
{
  "name": "create_ticket",
  "description": "Creates a support ticket",
  "api_config": {
    "url": "https://api.example.com/tickets",
    "method": "POST",
    "content_type": "application/json",
    "request_body_schema": {
      "type": "object",
      "properties": {
        "subject": { "type": "string" },
        "priority": { "type": "integer", "description": "1-5" }
      },
      "required": ["subject"]
    }
  }
}
```

---

## 7. POST with Array Body

When a field in the body is an array of items.

**Request Body:**
```json
{
  "name": "add_tags_to_post",
  "description": "Adds multiple tags to a specific post",
  "api_config": {
    "url": "https://api.example.com/posts/{post_id}/tags",
    "method": "POST",
    "content_type": "application/json",
    "path_params_schema": {
      "post_id": { "type": "integer" }
    },
    "request_body_schema": {
      "type": "object",
      "properties": {
        "tags": {
          "type": "array",
          "items": { "type": "string" }
        }
      }
    }
  }
}
```

---

## 8. POST with Nested Object Body

Complex nested JSON structures.

**Request Body:**
```json
{
  "name": "update_user_profile",
  "description": "Updates user profile with nested address object",
  "api_config": {
    "url": "https://api.example.com/user/profile",
    "method": "POST",
    "content_type": "application/json",
    "request_body_schema": {
      "type": "object",
      "properties": {
        "full_name": { "type": "string" },
        "address": {
          "type": "object",
          "properties": {
            "city": { "type": "string" },
            "zipcode": { "type": "integer" }
          }
        }
      }
    }
  }
}
```

---

## Webhook Example (Response Variables)

Capturing data from the API response to use later in the conversation.

**Request Body:**
```json
{
  "name": "order_pizza",
  "description": "Orders a pizza and captures the order ID",
  "api_config": {
    "url": "https://api.pizza.com/order",
    "method": "POST",
    "content_type": "application/json",
    "request_body_schema": {
      "type": "object",
      "properties": {
        "topping": { "type": "string" }
      }
    },
    "response_variables": {
      "order_number": "order_id",
      "estimated_time": "delivery_info.eta"
    }
  }
}
```
