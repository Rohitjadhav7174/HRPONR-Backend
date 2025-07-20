from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError, ConnectionFailure
from bson import ObjectId
import os
import re
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="Ecommerce Backend", description="FastAPI backend for ecommerce application")

# MongoDB connection with error handling
MONGODB_URL = os.getenv("MONGODB_URL") or "mongodb://localhost:27017"
DATABASE_NAME = "ecommerce"

# Debug: Print the MongoDB URL being used
print(f"Using MongoDB URL: {MONGODB_URL}")

def get_database():
    """Get database connection with error handling"""
    try:
        client = MongoClient(MONGODB_URL, serverSelectionTimeoutMS=5000)
        # Test the connection
        client.admin.command('ping')
        db = client[DATABASE_NAME]
        return db, client
    except (ServerSelectionTimeoutError, ConnectionFailure) as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        raise HTTPException(
            status_code=503, 
            detail=f"Database connection failed. Please ensure MongoDB is running at {MONGODB_URL}"
        )

# Initialize database connection
try:
    db, client = get_database()
    products_collection = db.products
    orders_collection = db.orders
    logger.info("Successfully connected to MongoDB")
except HTTPException as e:
    logger.warning("MongoDB not available at startup, will retry on first request")
    db = None
    client = None
    products_collection = None
    orders_collection = None

# Pydantic models for request/response validation
class SizeInfo(BaseModel):
    size: str
    quantity: int

class ProductCreate(BaseModel):
    name: str
    price: float
    sizes: List[SizeInfo]

class ProductResponse(BaseModel):
    id: str
    name: str
    price: float

class PaginationInfo(BaseModel):
    next: Optional[str] = None
    limit: int
    previous: Optional[int] = None

class ProductsListResponse(BaseModel):
    data: List[Dict[str, Any]]
    page: PaginationInfo

class OrderItem(BaseModel):
    productId: str
    qty: int

class OrderCreate(BaseModel):
    userId: str
    items: List[OrderItem]

class OrderResponse(BaseModel):
    id: str

class OrderDetails(BaseModel):
    productDetails: Dict[str, Any]
    qty: int

class OrderData(BaseModel):
    id: str
    items: List[OrderDetails]
    total: float

class OrdersListResponse(BaseModel):
    data: List[OrderData]
    page: PaginationInfo

def ensure_db_connection():
    """Ensure database connection is available"""
    global db, client, products_collection, orders_collection
    
    if db is None or client is None:
        try:
            db, client = get_database()
            products_collection = db.products
            orders_collection = db.orders
        except HTTPException:
            raise

# Helper function to convert ObjectId to string
def serialize_doc(doc):
    if doc:
        doc['_id'] = str(doc['_id'])
        return doc
    return None

# Create Products API
@app.post("/products", status_code=201)
async def create_product(product: ProductCreate):
    """Create a new product"""
    ensure_db_connection()
    
    try:
        # Convert sizes list to the required format
        sizes_data = [{"size": size.size, "quantity": size.quantity} for size in product.sizes]
        
        product_doc = {
            "name": product.name,
            "price": product.price,
            "sizes": sizes_data,
            "created_at": datetime.utcnow()
        }
        
        result = products_collection.insert_one(product_doc)
        
        return {"id": str(result.inserted_id)}
        
    except Exception as e:
        logger.error(f"Error creating product: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# List Products API
@app.get("/products")
async def list_products(
    name: Optional[str] = Query(None, description="Filter by product name (supports regex)"),
    size: Optional[str] = Query(None, description="Filter by size availability"),
    limit: int = Query(10, ge=1, le=100, description="Number of products to return"),
    offset: int = Query(0, ge=0, description="Number of products to skip")
):
    """List products with filtering and pagination"""
    ensure_db_connection()
    
    try:
        # Build filter query
        filter_query = {}
        
        if name:
            # Support regex search for name
            filter_query["name"] = {"$regex": name, "$options": "i"}
        
        if size:
            # Filter products that have the specified size
            filter_query["sizes.size"] = size
        
        # Count total documents for pagination
        total_count = products_collection.count_documents(filter_query)
        
        # Get products with pagination
        cursor = products_collection.find(filter_query).skip(offset).limit(limit)
        products = []
        
        for doc in cursor:
            product_data = {
                "id": str(doc["_id"]),
                "name": doc["name"],
                "price": doc["price"]
                # Note: sizes are not included in output as per the specification
            }
            products.append(product_data)
        
        # Pagination info
        next_offset = None
        if offset + limit < total_count:
            next_offset = str(offset + limit)
        
        previous_offset = None
        if offset > 0:
            previous_offset = max(0, offset - limit)
        
        pagination = {
            "next": next_offset,
            "limit": limit,
            "previous": previous_offset
        }
        
        return {
            "data": products,
            "page": pagination
        }
        
    except Exception as e:
        logger.error(f"Error listing products: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Create Order API
@app.post("/orders", status_code=201)
async def create_order(order: OrderCreate):
    """Create a new order"""
    ensure_db_connection()
    
    try:
        # Validate that all products exist
        product_ids = [ObjectId(item.productId) for item in order.items]
        existing_products = list(products_collection.find({"_id": {"$in": product_ids}}))
        
        if len(existing_products) != len(product_ids):
            raise HTTPException(status_code=400, detail="One or more products not found")
        
        # Create order document
        order_doc = {
            "userId": order.userId,
            "items": [{"productId": item.productId, "qty": item.qty} for item in order.items],
            "created_at": datetime.utcnow(),
            "status": "created"
        }
        
        result = orders_collection.insert_one(order_doc)
        
        return {"id": str(result.inserted_id)}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating order: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Get List of Orders
@app.get("/orders/{user_id}")
async def get_user_orders(
    user_id: str,
    limit: int = Query(10, ge=1, le=100, description="Number of orders to return"),
    offset: int = Query(0, ge=0, description="Number of orders to skip")
):
    """Get orders for a specific user"""
    ensure_db_connection()
    
    try:
        # Filter orders by user_id
        filter_query = {"userId": user_id}
        
        # Count total documents for pagination
        total_count = orders_collection.count_documents(filter_query)
        
        # Get orders with pagination
        cursor = orders_collection.find(filter_query).skip(offset).limit(limit)
        orders_data = []
        
        for order_doc in cursor:
            # Get product details for each item in the order
            order_items = []
            total_amount = 0.0
            
            for item in order_doc["items"]:
                product_id = ObjectId(item["productId"])
                product = products_collection.find_one({"_id": product_id})
                
                if product:
                    product_details = {
                        "name": product["name"],
                        "id": item["productId"]
                    }
                    
                    order_item = {
                        "productDetails": product_details,
                        "qty": item["qty"]
                    }
                    order_items.append(order_item)
                    total_amount += product["price"] * item["qty"]
            
            order_data = {
                "id": str(order_doc["_id"]),
                "items": order_items,
                "total": total_amount
            }
            orders_data.append(order_data)
        
        # Pagination info
        next_offset = None
        if offset + limit < total_count:
            next_offset = str(offset + limit)
        
        previous_offset = None
        if offset > 0:
            previous_offset = max(0, offset - limit)
        
        pagination = {
            "next": next_offset,
            "limit": limit,
            "previous": previous_offset
        }
        
        return {
            "data": orders_data,
            "page": pagination
        }
        
    except Exception as e:
        logger.error(f"Error getting user orders: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Health check endpoint
@app.get("/")
async def root():
    return {"message": "Ecommerce Backend API is running"}

@app.get("/health")
async def health_check():
    try:
        # Test database connection
        if client is None:
            ensure_db_connection()
        
        client.admin.command('ping')
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail=f"Database connection failed: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)