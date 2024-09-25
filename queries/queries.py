import pprint
import json
import os
from bson.objectid import ObjectId
from pymongo import MongoClient, GEOSPHERE
import pandas as pd
import matplotlib.pyplot as plt
import datetime
import geopy.distance

# ---------------- SETUP ---------------- #
usr = "YOUR_USERNAME"
pswd = "YOUR_PASSWORD"
conn_str = f"mongodb+srv://{usr}:{pswd}@uom-databases.8jvsagd.mongodb.net/?retryWrites=true&w=majority"
client = MongoClient(conn_str, serverSelectionTimeoutMS=5000)
try:
    print(client.server_info())
except Exception as e:
    print("Unable to connect to the server: ", e)

# ---------------- RESETTING DB ---------------- #

db = client["YOUR_DB_NAME"]

# Reset and create collections
collections = {
    "addresses": "addresses.json",
    "customers": "customers.json",
    "delivery_tasks": "delivery_tasks.json",
    "inventory_logs" : "inventory_logs.json",
    "partners": "partners.json",
    "past_orders": "past_orders.json",
    "products" : "products.json",
    "ratings" : "ratings.json",
    "stores" : "stores.json",
}
for collection in collections.keys():
    db[collection].drop()

# Load data from files in the 'collections/' directory
for collection, filename in collections.items():
    file_path = os.path.join("../collections", filename)
    with open(file_path, 'r') as file:
        data = json.load(file)
        if data:  # Check if data exists
            db[collection].insert_many(data)

print("All collections have been reloaded.")

# ================================== Query 1 ================================== #

def calculate_eta(start_coords, end_coords, velocity_kmph=50):
        distance = geopy.distance.distance(start_coords, end_coords).km
        eta_hours = distance / velocity_kmph
        return eta_hours * 60  # in minutes

def assign_order_and_partner(db, client_id, product_ids):

    # Ensure indexes are created -> faster queries per IDs
    db.stores.create_index([("location", GEOSPHERE)])
    db.partners.create_index([("location", GEOSPHERE)])
    db.stores.create_index("inventory.productID")

    # Get client location
    client_location = db.customers.find_one({"_id": client_id})["location"]

    # Find the nearest store with all the requested products
    try:
        nearest_store = db.stores.aggregate([
            {"$geoNear": {
                "near": client_location,
                "distanceField": "dist.calculated",
                "spherical": True,
            }},
            {"$match": {"inventory.productID": {"$all": product_ids}}},
            {"$limit": 1}
        ]).next()

        # Check if the store has all products
        available_product_ids = {item['productID'] for item in nearest_store['inventory']}
        if not all(pid in available_product_ids for pid in product_ids):
            return "No available store found!"
    except StopIteration:
        return "No store with all items found!"

    # Find the nearest available delivery partner
    nearest_partner = db.partners.aggregate([
        {"$geoNear": {
            "near": nearest_store['location'],
            "distanceField": "dist.calculated",
            "spherical": True
        }},
        {"$limit": 1}
    ]).next()

    # Constructing the order details
    order = {
        "_id": ObjectId(),
        "totalOrderCost": sum([product['stdPrice'] for product in nearest_store['inventory'] if product['productID'] in product_ids]),
        "status": "Pending",
        "orderItems": [{"productID": pid, "quantity": 1} for pid in product_ids] # assume quantity 1
    }

    # Update customer's currentOrders
    db.customers.update_one(
        {"_id": client_id},
        {"$push": {"currentOrders": order}}
    )

    order['orderItems'] =  [{"productID": pid, "quantity": 1, "name": db.products.find_one({"_id": pid})["name"],
                              "shortDescription": db.products.find_one({"_id": pid})["shortDescription"], 
                              "stdPrice": db.products.find_one({"_id": pid})["stdPrice"]} for pid in product_ids]
    
    # Update partner's deliveryTasks
    customer_data = db.customers.find_one({"_id": client_id})
    customer_shipping_address = ', '.join(customer_data['defaultAddresses']['shipping'].values())
    delivery_task = {
        "_id": order["_id"],
        "deliveryAddress": customer_shipping_address,
        "totalOrderCost": order["totalOrderCost"],
        "dateOfDelivery": datetime.datetime.now(),
        "deliveryStatus": "Pending",
        "store": {
            "_id": nearest_store["_id"],
            "name": nearest_store["name"],
            "address": nearest_store["address"]
        },
        "orderItems": order["orderItems"]
    }
    db.partners.update_one(
        {"_id": nearest_partner["_id"]},
        {"$push": {"deliveryTasks": delivery_task}}
    )
    # Convert coordinates to tuples and calculate ETA
    store_coords = tuple(nearest_store['location']['coordinates'][::-1])
    customer_coords = tuple(client_location['coordinates'][::-1])
    order['eta'] = calculate_eta(store_coords, customer_coords)
    for item in order['orderItems']:
        product_data = db.products.find_one({"_id": item["productID"]})
        item["avgRating"] = product_data.get("avgRatingScore", 0)


    # Return the order details and other relevant info
    return {
        "order_details": order,
        "customer_address": customer_shipping_address,
        "partner_details": {
            "name": nearest_partner["name"],
            "location": nearest_partner["location"],
        },
        "store_details": {
            "name": nearest_store["name"],
            "location": nearest_store["location"],
        }
    }

# Example usage
"""
print("="*150)
assigned_partner_return = assign_order_and_partner(db, "0d4a13c3-c9ef-40f2-8516-58de00809364", ["345d1a0e-a274-44fe-875c-901a5d01bedc"])
pprint.pprint(assigned_partner_return)
print("="*150)
assigned_partner_return = assign_order_and_partner(db, "7c36c0d0-8092-4579-a063-6828e7d2f743", ['975c40d3-8c4a-46c7-9bc7-d7e826f7d173', "173c12ff-75e0-44be-8c9a-d6136a67bd08"])
pprint.pprint(assigned_partner_return)
print("="*150)
assigned_partner_return = assign_order_and_partner(db, "cbb2b56d-af38-466a-be7c-7c6743092fde", ['975c40d3-8c4a-46c7-9bc7-d7e826f7d173', "173c12ff-75e0-44be-8c9a-d6136a67bd08", "345d1a0e-a274-44fe-875c-901a5d01bedc"])
pprint.pprint(assigned_partner_return)

======================================================================================================================================================
{'customer_address': '049, Jay crest, Manchester, M70ND',
 'order_details': {'_id': ObjectId('657c2b1c556e9d1032158d5a'),
                   'eta': 19718.071064793796,
                   'orderItems': [{'avgRating': 2.71,
                                   'name': 'Megumi Mushrooms',
                                   'productID': '345d1a0e-a274-44fe-875c-901a5d01bedc',
                                   'quantity': 1,
                                   'shortDescription': 'Indigenous Korean '
                                                       'funghi.',
                                   'stdPrice': 650}],
                   'status': 'Pending',
                   'totalOrderCost': 650},
 'partner_details': {'location': {'coordinates': [-119.02763, -56.4281105],
                                  'type': 'Point'},
                     'name': 'Camille Burke-Ashton'},
 'store_details': {'location': {'coordinates': [2.617389, -62.821071],
                                'type': 'Point'},
                   'name': 'Brookes PLC'}}
======================================================================================================================================================
{'customer_address': '7, Helen parkways, Manchester, M217W',
 'order_details': {'_id': ObjectId('657c2b1c556e9d1032158d5b'),
                   'eta': 13351.987941785199,
                   'orderItems': [{'avgRating': 3.71,
                                   'name': 'Serbian Rolls',
                                   'productID': '975c40d3-8c4a-46c7-9bc7-d7e826f7d173',
                                   'quantity': 1,
                                   'shortDescription': 'Family owned recipe, '
                                                       'baked fresh every day.',
                                   'stdPrice': 440},
                                  {'avgRating': 3.01,
                                   'name': 'Farmers Bread',
                                   'productID': '173c12ff-75e0-44be-8c9a-d6136a67bd08',
                                   'quantity': 1,
                                   'shortDescription': 'Freshly baked, from '
                                                       'the field to your '
                                                       'home.',
                                   'stdPrice': 420}],
                   'status': 'Pending',
                   'totalOrderCost': 860},
 'partner_details': {'location': {'coordinates': [-90.258552, 78.204976],
                                  'type': 'Point'},
                     'name': 'Joseph Jones'},
 'store_details': {'location': {'coordinates': [-75.598672, 26.9720805],
                                'type': 'Point'},
                   'name': 'Jones-Baxter'}}
======================================================================================================================================================
'No store with all items found!'

"""

# ================================== Query 2 ================================== #

def find_fresh_products(db, user_id, max_distance, productType):

    # Ensure the index is set for geospatial queries
    db.stores.create_index([("location", GEOSPHERE)])

    # Create aggregation pipeline
    pipeline = [
        {
            "$geoNear": {
                "near": db.customers.find_one({"_id": user_id}, {"location": 1})["location"],
                "distanceField": "distance",
                "maxDistance": max_distance,
                "spherical": True
            }
        },
        {
            "$unwind": "$inventory"
        },
        {
            "$lookup": {
                "from": "products",
                "localField": "inventory.productID",
                "foreignField": "_id",
                "as": "productDetails"
            }
        },
        {
            "$unwind": "$productDetails"
        },
        {
            "$match": {"productDetails.productSegment": productType}
        },
    {
        "$project": {
            "Product name": "$productDetails.name",
            "Product category": "$productDetails.attributes.freshAttributes.category",
            "Country Of Origin": "$productDetails.attributes.freshAttributes.countryOfOrigin",
            "Expiry Date": "$productDetails.attributes.freshAttributes.expiryDate",
            "Average Rating": "$productDetails.avgRatingScore",
            "Dimensions": "$productDetails.dimensions",
            "Product Description": "$productDetails.shortDescription",
            "Product Price": "$productDetails.stdPrice",
            "_id": 0
        }
    }
    ]

    result = list(db.stores.aggregate(pipeline))
    return result

# Example usage
"""
print("="*150)
fresh_products = find_fresh_products(db, "0d4a13c3-c9ef-40f2-8516-58de00809364", 5000000, "Fresh")
pprint.pprint(fresh_products)
print("="*150)
fresh_products = find_fresh_products(db, "cbb2b56d-af38-466a-be7c-7c6743092fde", 5000000, "Fresh")
pprint.pprint(fresh_products)

======================================================================================================================================================
[{'Average Rating': 2.76,
  'Country Of Origin': 'Burundi',
  'Dimensions': '10x12x8 cm',
  'Expiry Date': '2023-12-28',
  'Product Description': 'Soft bread with chocolate pieces inside.',
  'Product Price': 484,
  'Product category': 'Bakery',
  'Product name': 'Chocolate Bread'},
 {'Average Rating': 3.53,
  'Country Of Origin': 'Japan',
  'Dimensions': '17x95x65 cm',
  'Expiry Date': '2024-01-04',
  'Product Description': 'Best soda from Japan, with a twist!',
  'Product Price': 260,
  'Product category': 'Drinks',
  'Product name': 'Monsune Soda'},
 {'Average Rating': 1.27,
  'Country Of Origin': 'Macao',
  'Dimensions': '71x30x54 cm',
  'Expiry Date': '2023-12-19',
  'Product Description': 'Only best quality cocoa, ethically sourced milk.',
  'Product Price': 488,
  'Product category': 'Drinks',
  'Product name': 'Chocolate Milk'},
 {'Average Rating': 3.53,
  'Country Of Origin': 'Japan',
  'Dimensions': '17x95x65 cm',
  'Expiry Date': '2024-01-04',
  'Product Description': 'Best soda from Japan, with a twist!',
  'Product Price': 260,
  'Product category': 'Drinks',
  'Product name': 'Monsune Soda'}]
======================================================================================================================================================
[{'Average Rating': 3.53,
  'Country Of Origin': 'Japan',
  'Dimensions': '17x95x65 cm',
  'Expiry Date': '2024-01-04',
  'Product Description': 'Best soda from Japan, with a twist!',
  'Product Price': 260,
  'Product category': 'Drinks',
  'Product name': 'Monsune Soda'}]

"""

# ================================== Query 3 ================================== #

def place_order(db, customer_id, product_ids):
    products = db.products

    # Convert dict to an array of { _id, quantity }
    product_entries = [{"_id": pid, "quantity": qty} for pid, qty in product_ids.items()]

    # Create aggregation pipeline
    pipeline = [
        {"$match": {"_id": {"$in": [pid for pid in product_ids.keys()]}}},
        {"$addFields": {
            "quantity": {
                "$filter": {
                    "input": product_entries,
                    "as": "entry",
                    "cond": {"$eq": ["$$entry._id", "$_id"]}
                }
            }
        }},
        {"$unwind": "$quantity"},
        {"$set": {"quantity": "$quantity.quantity"}},
        {"$group": {
            "_id": None,
            "Total Cost": {"$sum": {"$multiply": ["$stdPrice", "$quantity"]}},
            "order_items": {"$push": {
                "productID": "$_id",
                "quantity": "$quantity"
            }},
            "order_items_names": {"$push": {
                "name": "$name",
                "quantity": "$quantity"
            }}
        }}
    ]

    aggregation_result = list(products.aggregate(pipeline))
    if not aggregation_result:
        return "No products found"

    total_cost = aggregation_result[0]["Total Cost"]
    order_items = aggregation_result[0]["order_items"]
    order_items_names = aggregation_result[0]["order_items_names"]

    # Create new order
    new_order = {
        "_id": ObjectId(),
        "totalOrderCost": total_cost,
        "status": "Pending",
        "orderItems": order_items  # Storing product IDs for customer records
    }

    # Add the order to the customer's currentOrders
    db.customers.update_one({"_id": customer_id}, {"$push": {"currentOrders": new_order}})

    # Project order details for the end user
    order_details = {
        "order_id": new_order["_id"],
        "Total Cost": total_cost,
        "items": order_items_names
    }
    return order_details

# Example usage
"""
print("="*150)
product_ids = {"0b9923f0-6f51-4cfa-ac52-3367409a57a4":3, "71227cf8-5f05-45e8-b98d-8fdb3131e6e5":1, "fbf553e8-2eaa-4e01-9d55-9da1a366cc5f":2}
order_details = place_order(db, "0d4a13c3-c9ef-40f2-8516-58de00809364", product_ids)
pprint.pprint(order_details)
print("="*150)
product_ids = {"fbf553e8-2eaa-4e01-9d55-9da1a366cc5f":1, "thisIdWontWork!": 2}
order_details = place_order(db, "0d4a13c3-c9ef-40f2-8516-58de00809364", product_ids)
pprint.pprint(order_details)

======================================================================================================================================================
{'Total Cost': 27260,
 'items': [{'name': 'Mea Culpa', 'quantity': 3},
           {'name': 'All Liquid', 'quantity': 1},
           {'name': 'Ad Laborum', 'quantity': 2}],
 'order_id': ObjectId('657c2b6c4621857590f24f29')}
======================================================================================================================================================
{'Total Cost': 6750,
 'items': [{'name': 'Ad Laborum', 'quantity': 1}],
 'order_id': ObjectId('657c2b6c4621857590f24f2a')}

"""

# ================================== Query 4 ================================== #

def check_and_plot_inventory_by_date(db, product_id):

    # Fetch the product name
    product = db.products.find_one({"_id": product_id}, {"name": 1})
    if not product:
        print("Product not found.")
        return
    product_name = product['name']

    # Fetch inventory data grouped by warehouse and date
    pipeline = [
        {"$match": {"productID": product_id}},
        {"$group": {
            "_id": {"warehouse": "$storageWarehouseName", "date": "$date"},
            "totalInventory": {"$sum": "$inventoryQuantity"}
        }},
        {"$sort": {"_id.date": 1}}  # sort by date
    ]
    inventory_data = list(db.inventory_logs.aggregate(pipeline))

    # Check if data is available
    if not inventory_data:
        print("No inventory data found for this product.")
        return

    # Convert to DataFrame
    df = pd.DataFrame([{"warehouse": data['_id']['warehouse'], "date": data['_id']['date'], "totalInventory": data['totalInventory']} for data in inventory_data])

    # Pivot the DataFrame for plotting
    df_pivot = df.pivot(index='date', columns='warehouse', values='totalInventory').fillna(0)

    # Plotting
    df_pivot.plot(kind='bar', figsize=(12, 8))
    plt.xlabel('Date')
    plt.ylabel('Total Inventory')
    plt.title(f'Inventory Levels for "{product_name}" (Product ID: {product_id}) by Date and Warehouse')
    plt.xticks(rotation=45)
    plt.legend(title='Warehouse')
    plt.show()

def plot_sales_per_user(db, customer_id):
    # Fetch sales data per user and per item with cost and profit
    pipeline = [
        {"$match": {"customerID": customer_id}},
        {"$unwind": "$orderItems"},
        {"$lookup": {
            "from": "products",
            "localField": "orderItems.productID",
            "foreignField": "_id",
            "as": "productDetails"
        }},
        {"$unwind": "$productDetails"},
        {"$group": {
            "_id": "$orderItems.productName",
            "totalCost": {"$sum": {"$multiply": ["$orderItems.quantity", "$productDetails.supplierPrice"]}},
            "totalProfit": {"$sum": {"$multiply": ["$orderItems.quantity", {"$subtract": ["$productDetails.stdPrice", "$productDetails.supplierPrice"]}]}},
        }},
        {"$project": {
            "totalSales": {"$add": ["$totalCost", "$totalProfit"]},
            "totalCost": 1,
            "totalProfit": 1
        }},
        {"$sort": {"totalSales": -1}}
    ]
    sales_data = list(db.past_orders.aggregate(pipeline))

    # Check if data is available
    if not sales_data:
        print("No sales data found for this user.")
        return

    # Convert to pandas.df
    df = pd.DataFrame(sales_data)

    # Plotting
    plt.figure(figsize=(12, 8))
    # Stacking profit on top of cost
    plt.bar(df['_id'], df['totalCost'], label='Cost', color='red')
    plt.bar(df['_id'], df['totalProfit'], bottom=df['totalCost'], label='Profit', color='green')
    plt.xlabel('Product Name')
    plt.ylabel('Total in Cents')
    plt.title(f'Total Cost and Profit per Item for User ID: {customer_id}')
    plt.xticks(rotation=45)
    plt.legend()
    plt.show()

def plot_sales_per_product(db, product_ids):
    # Fetch sales data per product with cost and profit
    pipeline = [
        {"$unwind": "$orderItems"},
        {"$match": {"orderItems.productID": {"$in": product_ids}}},
        {"$lookup": {
            "from": "products",
            "localField": "orderItems.productID",
            "foreignField": "_id",
            "as": "productDetails"
        }},
        {"$unwind": "$productDetails"},
        {"$group": {
            "_id": "$productDetails.name",
            "totalCost": {"$sum": {"$multiply": ["$orderItems.quantity", "$productDetails.supplierPrice"]}},
            "totalRevenue": {"$sum": {"$multiply": ["$orderItems.quantity", "$productDetails.stdPrice"]}}
        }},
        {"$project": {
            "totalCost": 1,
            "totalProfit": {"$subtract": ["$totalRevenue", "$totalCost"]},
            "totalRevenue": 1
        }},
        {"$sort": {"totalRevenue": -1}}
    ]
    sales_data = list(db.past_orders.aggregate(pipeline))

    # Check if data is available
    if not sales_data:
        print("No sales data found for these products.")
        return

    # Convert to pandas.df
    df = pd.DataFrame(sales_data)

    # Plotting
    plt.figure(figsize=(12, 8))
    # Stacking profit on top of cost
    plt.bar(df['_id'], df['totalCost'], label='Cost', color='red')
    plt.bar(df['_id'], df['totalProfit'], bottom=df['totalCost'], label='Profit', color='green')
    plt.xlabel('Product Name')
    plt.ylabel('Total in Cents')
    plt.title('Total Cost, Revenue, and Profit per Product')
    plt.xticks(rotation=45)
    plt.legend()
    plt.show()

# Example usage
"""
check_and_plot_inventory_by_date(db, "0b9923f0-6f51-4cfa-ac52-3367409a57a4")
plot_sales_per_user(db, "0d4a13c3-c9ef-40f2-8516-58de00809364")
plot_sales_per_product(db, ["0b9923f0-6f51-4cfa-ac52-3367409a57a4", "fbf553e8-2eaa-4e01-9d55-9da1a366cc5f", "0f8ca27a-3c13-4a75-85db-0abaedf2fa5f"])

Results can be seen in ./figures under Figure_1, Figure_2, and Figure_3
"""

#  ================================== Additional Queries  ================================== #

# ================================== Query 5 ================================== #
# Update avgRatings for all products (can be done offline, or periodically i.e. every 1 hour/day)
def update_product_ratings(db):
    # Create necessary indexes for optimization (per IDs)
    db.ratings.create_index("productID")
    db.products.create_index("_id")

    # Create aggregation pipeline to calculate average ratings
    pipeline = [
        {"$group": {
            "_id": "$productID",
            "avgRating": {"$avg": "$score"}
        }},
        {"$merge": {
            "into": "tempAvgRatings",
            "on": "_id",
            "whenMatched": "replace",
            "whenNotMatched": "insert"
        }}
    ]
    db.ratings.aggregate(pipeline)

    # Update the products collection
    for avg_rating_doc in db.tempAvgRatings.find():
        db.products.update_one(
            {"_id": avg_rating_doc["_id"]},
            {"$set": {"avgRatingScore": avg_rating_doc["avgRating"]}}
        )
    # Drop temporary collection after the update
    db.tempAvgRatings.drop()

# Example usage
"""
update_product_ratings(db)
This one does not have a return due to purely updating fields within the database.
"""


# ================================== Query 6 ================================== #

# Move closed orders from customer into past orders
def move_closed_orders_to_past_orders_for_customer(db, customer_id):
    # Fetch the specific customer's data
    customer = db.customers.find_one({"_id": customer_id})

    if not customer:
        return "Customer not found."

    closed_orders = [order for order in customer.get("currentOrders", []) if order["status"] == "Closed"]

    # Skip if there are no closed orders (i.e. mistake from delivery)
    if not closed_orders:
        return "No closed orders for this customer."

    # Process each closed order
    past_order_ids = []
    for order in closed_orders:
        # Prepare the order for PastOrders
        past_order = {
            "_id": ObjectId(),
            "customerID": customer["_id"],
            "totalOrderCost": order["totalOrderCost"],
            "orderItems": order["orderItems"]
        }
        db.past_orders.insert_one(past_order)
        past_order_ids.append(past_order["_id"])

    # Update Customer document
    db.customers.update_one(
        {"_id": customer_id},
        {
            "$pull": {"currentOrders": {"status": "Closed"}},
            "$addToSet": {"pastOrders": {"$each": past_order_ids}}
        }
    )
    return "Closed orders moved to past orders for customer."
# Example usage
"""
print("="*150)
print(move_closed_orders_to_past_orders_for_customer(db, "0d4a13c3-c9ef-40f2-8516-58de00809364"))

No closed orders for this customer.
"""

# ================================== Query 7 ================================== #

# Similar to above, this time we move tasks from partners into DeliveryTasks per schema
def move_completed_delivery_tasks(db, partner_name=None, partner_id=None):
    # Fetch the specific partner's data
    if partner_id:
        partner = db.partners.find_one({"_id": partner_id})
    elif partner_name:
        partner = db.partners.find_one({"name": partner_name})
    else:
        return "Please provide partner details!"

    if not partner:
        return "Partner not found."

    # Identify delivery tasks to be moved
    tasks_to_move = [task for task in partner.get("deliveryTasks", []) 
                     if task["deliveryStatus"] in ["Complete", "Canceled", "Customer Canceled", "Rescheduled"]]

    # Skip if there are no tasks to move
    if not tasks_to_move:
        return "No relevant delivery tasks for this partner."

    # Process each delivery task
    for task in tasks_to_move:
        # Add partner reference
        task["partner"] = partner["_id"]

        # Insert into DeliveryTasks collection
        db.delivery_tasks.insert_one(task)

    # Update Partner document by removing the moved tasks
    if partner_id:
        db.partners.update_one(
        {"_id": partner_id},
        {"$pull": {"deliveryTasks": {"deliveryStatus": {"$in": ["Complete", "Canceled", "Customer Canceled", "Rescheduled"]}}}}
    )
    elif partner_name:
        db.partners.update_one(
        {"name": partner_name},
        {"$pull": {"deliveryTasks": {"deliveryStatus": {"$in": ["Complete", "Canceled", "Customer Canceled", "Rescheduled"]}}}}
    )

    return "Relevant delivery tasks moved for partner."

# Example usage
"""
print("="*150)
print(move_completed_delivery_tasks(db, partner_name="Joseph Jones", partner_id="74426dcf-ce2a-4ff9-8482-c52314e09772"))

Relevant delivery tasks moved for partner.
"""

# ================================== Query 8 ================================== #

# Plot the N least frequent and least rated products
def find_and_plot_product_stats(db, prod_limit):

    # Query for the top 10 lowest rated products using avgRatingScore in Products
    lowest_rated_products = list(db.products.find(
        {},
        {"name": 1, "avgRatingScore": 1}
    ).sort("avgRatingScore", 1).limit(prod_limit))

    # Query for the top 10 least frequent products in past orders
    pipeline_least_frequent = [
        {"$unwind": "$orderItems"},
        {"$group": {
            "_id": "$orderItems.productID",
            "count": {"$sum": 1}
        }},
        {"$sort": {"count": 1}},
        {"$limit": prod_limit},
        {"$lookup": {
            "from": "products",
            "localField": "_id",
            "foreignField": "_id",
            "as": "productInfo"
        }},
        {"$unwind": "$productInfo"},
        {"$project": {
            "productName": "$productInfo.name",
            "count": 1
        }}
    ]
    least_frequent_products = list(db.past_orders.aggregate(pipeline_least_frequent))

    # Plotting
    # Plot for lowest rated products
    plt.figure(figsize=(10, 6))
    plt.bar([product['name'] for product in lowest_rated_products], 
            [product['avgRatingScore'] for product in lowest_rated_products], 
            color='blue')
    plt.xlabel('Products')
    plt.ylabel('Average Rating Score')
    plt.title('Top 10 Lowest Rated Products')
    plt.xticks(rotation=45)
    plt.tight_layout()

    # Plot for least frequent products
    plt.figure(figsize=(10, 6))
    plt.bar([product['productName'] for product in least_frequent_products], 
            [product['count'] for product in least_frequent_products], 
            color='red')
    plt.xlabel('Products')
    plt.ylabel('Frequency in Past Orders')
    plt.title('Top 10 Least Frequent Products')
    plt.xticks(rotation=45)
    plt.tight_layout()

    plt.show()

# Example usage
"""
find_and_plot_product_stats(db, prod_limit=10)

Results can be seen in ./figures under Figure_4, and Figure_5
"""

# ================================== Query 9 ================================== #

# Get top N items with lowest inventories, alongside shop names
def find_stores_with_lowest_inventory_items(db, no_of_stores):
    pipeline = [
        {"$unwind": "$inventory"},
        {"$group": {
            "_id": "$inventory.name",
            "lowestInventory": {"$min": "$inventory.availability"},
            "stores": {
                "$push": {
                    "storeName": "$name",
                    "availability": "$inventory.availability",
                    "address": "$address",
                    "location": "$location"
                }
            }
        }},
        {"$sort": {"lowestInventory": 1}},
        {"$limit": no_of_stores},
        {"$project": {
            "itemName": "$_id",
            "lowestInventory": 1,
            "stores": {
                "$filter": {
                    "input": "$stores",
                    "as": "store",
                    "cond": {"$eq": ["$$store.availability", "$lowestInventory"]}
                }
            }
        }}
    ]

    results = list(db.stores.aggregate(pipeline))

    # Formatting the result for better readability
    formatted_results = []
    for result in results:
        item_info = {
            "Item Name": result["itemName"],
            "Store Info with associated Inventory": [
                {
                    "Store Name": store["storeName"],
                    "Availability": store["availability"],
                    "Address": store["address"], 
                    "Location": store["location"]
                } for store in result["stores"]
            ]
        }
        formatted_results.append(item_info)
    return formatted_results

# Example usage
"""
low_inventory_items = find_stores_with_lowest_inventory_items(db, no_of_stores=5)
print("="*150)
for item in low_inventory_items:
    pprint.pprint(item)

{'Item Name': 'Monsune Soda',
 'Store Info with associated Inventory': [{'Address': 'Flat 96\n'
                                                      'Austin freeway\n'
                                                      'East Billy\n'
                                                      'W0 0YZ',
                                           'Availability': 23,
                                           'Location': {'coordinates': [106.338645,
                                                                        -56.2408145],
                                                        'type': 'Point'},
                                           'Store Name': 'Tomlinson Inc'}]}
{'Item Name': 'Orangingo Juice',
 'Store Info with associated Inventory': [{'Address': '122 Brown springs\n'
                                                      'Glennview\n'
                                                      'DT2B 5DH',
                                           'Availability': 36,
                                           'Location': {'coordinates': [-75.598672,
                                                                        26.9720805],
                                                        'type': 'Point'},
                                           'Store Name': 'Jones-Baxter'}]}
{'Item Name': 'Megumi Mushrooms',
 'Store Info with associated Inventory': [{'Address': 'Studio 98\n'
                                                      'Roberts plain\n'
                                                      'New Rita\n'
                                                      'L3W 4HA',
                                           'Availability': 41,
                                           'Location': {'coordinates': [2.617389,
                                                                        -62.821071],
                                                        'type': 'Point'},
                                           'Store Name': 'Brookes PLC'}]}
{'Item Name': 'Chocolate Milk',
 'Store Info with associated Inventory': [{'Address': '624 Davey shore\n'
                                                      'Jackshire\n'
                                                      'M55 8PF',
                                           'Availability': 43,
                                           'Location': {'coordinates': [-34.787872,
                                                                        58.012311],
                                                        'type': 'Point'},
                                           'Store Name': 'Brown-Bird'}]}
{'Item Name': 'Chocolate Bread',
 'Store Info with associated Inventory': [{'Address': '957 Rowley fort\n'
                                                      'North Conor\n'
                                                      'PL84 2LX',
                                           'Availability': 69,
                                           'Location': {'coordinates': [-147.240026,
                                                                        83.5615645],
                                                        'type': 'Point'},
                                           'Store Name': 'Powell, Metcalfe and '
                                                         'Jones'}]}    

"""

# End connection
client.close()
