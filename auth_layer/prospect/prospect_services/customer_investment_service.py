import time
from uuid import uuid4
from common_layer.common_services.utils import token_decoder, fcm_push_notification
from prospect_app.logging_module import logger
from database import db
from common_layer import constants
from http import HTTPStatus
from bson import ObjectId
from fastapi.encoders import jsonable_encoder
from common_layer.common_schemas.user_schema import ResponseMessage
from core_layer.aws_cloudfront.core_cloudfront import cloudfront_sign
from auth_layer.prospect.prospect_services import customer_management_service
from auth_layer.prospect.prospect_schemas.customer_investment_schema import (
    CustomerSharesInDb,
    CustomerTransactionSchemaInDB,
    CustomerFiatTransactionSchemaInDB,
    TransactionType,
)


def get_user_wallet(token):
    logger.debug("Inside Get User Wallet Service")
    try:
        logger.debug("Decoding Token")
        decoded_token = token_decoder(token)
        user_id = decoded_token.get(constants.ID)
        logger.debug("Getting User Wallet for User: " + str(user_id))
        user_wallet_collection = db[constants.USER_WALLET_SCHEMA]
        user_wallet = user_wallet_collection.find_one({"user_id": user_id})
        property_details_collection = db[constants.PROPERTY_DETAILS_SCHEMA]
        candlestick_data_collection = db[constants.CANDLE_DETAILS_SCHEMA]
        portfolio_analysis_collection = db[constants.PORTFOLIO_ANALYSIS_SCHEMA]
        if user_wallet is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "User Wallet Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response

        portfolio_details = []
        property_ids = []

        for property_id in user_wallet.keys():
            if property_id in ["_id", "user_id", "balance", "updated_at", "created_at"]:
                continue
            property_ids.append(ObjectId(property_id))

        property_details = property_details_collection.find(
            {constants.INDEX_ID: {"$in": property_ids}},
            {
                "project_title": 1,
                "address": 1,
                "price": 1,
                "project_logo": 1,
                "_id": 1,
                "roi_percentage": 1,
            },
        )
        property_id_list = [str(property_id) for property_id in property_ids]
        candlestick_data = candlestick_data_collection.find(
            {
                constants.PROPERTY_ID_FIELD: {
                    "$in": property_id_list
                }
            },
            {"candle_data": 1, "property_id": 1},
        )
        candle_dict = {}
        for candle in candlestick_data:
            candle_dict[str(candle.get(constants.PROPERTY_ID_FIELD))] = candle.get(
                "candle_data"
            )

        portfolio_analysis = portfolio_analysis_collection.find_one({constants.USER_ID_FIELD:user_id})
        if not portfolio_analysis:
            portfolio_analysis = {}

        portfolio_balance, investment_balance, avg_roi, yesterday_balance, today_balance = 0, 0, 0, 0, 0
        for property_detail in property_details:
            property_wallet_record = user_wallet.get(
                str(property_detail.get(constants.INDEX_ID))
            )
            wallet_quantity = property_wallet_record.get("quantity")
            investment_value = property_wallet_record.get("investment_value")
            if wallet_quantity > 0:
                yesterday_record = portfolio_analysis.get(str(property_detail.get(constants.INDEX_ID)))
                if not yesterday_record:
                    yesterday_balance += 0
                    yesterday_quantity = 0
                else:
                    yesterday_quantity = yesterday_record.get(constants.QUANTITY_FIELD)
                    yesterday_balance +=  yesterday_quantity * yesterday_record.get(constants.PRICE_FIELD)
                today_balance += yesterday_quantity * property_detail.get("price")
                portfolio_balance += wallet_quantity * property_detail.get("price")
                investment_balance += investment_value
                avg_roi += property_detail.get("roi_percentage")
                candle_data = candle_dict.get(
                    str(property_detail.get(constants.INDEX_ID))
                )
                change_percent_24_hr = (
                    0
                    if len(candle_data) == 1
                    else (
                        (candle_data[-1].get("price") - candle_data[-2].get("price"))
                        * 100
                        / candle_data[-2].get("price")
                    )
                )
                portfolio_details.append(
                    {
                        str(property_detail.get(constants.INDEX_ID)): {
                            "project_title": property_detail.get("project_title"),
                            "address": property_detail.get("address"),
                            "price": property_detail.get("price"),
                            "quantity": wallet_quantity,
                            "project_logo": cloudfront_sign(
                                property_detail.get("project_logo")
                            ),
                            "candle_data": candle_data,
                            "change_percent_24_hr": f"{(change_percent_24_hr):.2f}",
                            "investmen_value": investment_value,
                        }
                    }
                )
        
        day_change = (today_balance - yesterday_balance )
        day_change_percent =  day_change / yesterday_balance if yesterday_balance else 0
        total_change = (portfolio_balance- investment_balance )
        total_change_percent = total_change / investment_balance if investment_balance else 0 
        avg_roi = avg_roi / len(portfolio_details) if len(portfolio_details) else 0
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_SUCCESS,
            data={
                "portfolio_detail": portfolio_details,
                "balance": user_wallet.get("balance"),
                "portfolio_balance": portfolio_balance,
                "investment_balance": investment_balance,
                "day_change":day_change,
                "day_change_percent":day_change_percent * 100,
                "avg_roi": avg_roi,
                "total_change":total_change,
                "total_change_percent":total_change_percent * 100
            },
            status_code=HTTPStatus.OK,
        )
    except Exception as e:
        logger.error(f"Error in Get User Wallet Service: {e}")
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_FAILURE,
            data={constants.MESSAGE: f"Error in Get User Wallet Service: {e}"},
            status_code=e.status_code if hasattr(e, "status_code") else 500,
        )
    logger.debug("Returning From the Get User Wallet Service")
    return response


def add_balance(token, amount):
    logger.debug("Inside Add Balance Service")
    try:
        logger.debug("Decoding Token")
        decoded_token = token_decoder(token)
        user_id = decoded_token.get(constants.ID)
        logger.debug("Getting User Wallet for User: " + str(user_id))
        user_wallet_collection = db[constants.USER_WALLET_SCHEMA]
        customer_fiat_collection = db[constants.CUSTOMER_FIAT_TRANSACTIONS_SCHEMA]
        user_wallet = user_wallet_collection.find_one({"user_id": user_id})
        if user_wallet is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "User Wallet Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response
        old_balance = user_wallet.get("balance")
        balance = old_balance + amount
        user_wallet_collection.update_one(
            {"user_id": user_id}, {"$set": {"balance": balance}}
        )

        fiat_record = jsonable_encoder(
            CustomerFiatTransactionSchemaInDB(
                user_id=user_id,
                balance=old_balance,
                transaction_amount=amount,
                transaction_id=str(uuid4()),
                transaction_type=TransactionType.AMOUNT_DEPOSITED.value,
                transaction_status="SUCCESS",
                transaction_date=time.time(),
                created_at=time.time(),
                updated_at=time.time()
            )
        )

        customer_fiat_collection.insert_one(fiat_record)

        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_SUCCESS,
            data={"balance": balance},
            status_code=HTTPStatus.OK,
        )
        fcm_push_notification(
            user_id=user_id,
            title="Square",
            description=f"{amount} Rs Deposited in your wallet.",
            module="Invest",
            seconds=0,
            extra={},
        )
        customer_management_service.add_notifications("deposit", "Deposit", f"{amount} Rs Deposited in your wallet.", "wallet",  token)
    except Exception as e:
        logger.error(f"Error in Add Balance Service: {e}")
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_FAILURE,
            data={constants.MESSAGE: f"Error in Add Balance Service: {e}"},
            status_code=e.status_code if hasattr(e, "status_code") else 500,
        )
    logger.debug("Returning From the Add Balance Service")
    return response


def withdraw_balance(token, amount):
    logger.debug("Inside Withdraw Balance Service")
    try:
        logger.debug("Decoding Token")
        decoded_token = token_decoder(token)
        user_id = decoded_token.get(constants.ID)
        logger.debug("Getting User Wallet for User: " + str(user_id))
        user_wallet_collection = db[constants.USER_WALLET_SCHEMA]
        customer_fiat_collection = db[constants.CUSTOMER_FIAT_TRANSACTIONS_SCHEMA]
        user_wallet = user_wallet_collection.find_one({"user_id": user_id})
        if user_wallet is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "User Wallet Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response

        if amount < 0:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Amount Cannot be Negative. "},
                status_code=HTTPStatus.BAD_REQUEST,
            )
            return response

        if user_wallet.get("balance") < amount:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Amount Exceeded the Wallet Balance. "},
                status_code=HTTPStatus.BAD_REQUEST,
            )
            return response

        old_balance = user_wallet.get("balance")
        balance = old_balance - amount
        user_wallet_collection.update_one(
            {"user_id": user_id}, {"$set": {"balance": balance}}
        )

        fiat_record = jsonable_encoder(
            CustomerFiatTransactionSchemaInDB(
                user_id=user_id,
                balance=old_balance,
                transaction_amount=amount,
                transaction_id=str(uuid4()),
                transaction_type=TransactionType.AMOUNT_WITHDRAW.value,
                transaction_status="SUCCESS",
                transaction_date=time.time(),
                created_at=time.time(),
                updated_at=time.time()
            )
        )

        customer_fiat_collection.insert_one(fiat_record)
        customer_management_service.add_notifications("withdraw", "Withdraw", f"{amount} Rs Withdrawn from your wallet.", "wallet",  token)

        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_SUCCESS,
            data={"balance": balance},
            status_code=HTTPStatus.OK,
        )
    except Exception as e:
        logger.error(f"Error in Withdraw Balance Service: {e}")
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_FAILURE,
            data={constants.MESSAGE: f"Error in Withdraw Balance Service: {e}"},
            status_code=e.status_code if hasattr(e, "status_code") else 500,
        )
    logger.debug("Returning From the Withdraw Balance Service")
    return response


def fetch_available_shared(property_details):
    if property_details.get(constants.CATEGORY_FIELD) == "residential":
        residentail_category_collection = db[
            constants.RESIDENTIAL_PROPERTY_DETAILS_SCHEMA
        ]
        residential_category = residentail_category_collection.find_one(
            {
                constants.INDEX_ID: ObjectId(
                    property_details.get(constants.PROPERTY_DETAILS_ID_FIELD)
                )
            }
        )
        if residential_category is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Residential Category Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response
        current_available_shares = residential_category.get("carpet_area")
    elif property_details.get(constants.CATEGORY_FIELD) == "commercial":
        commercial_category_collection = db[
            constants.COMMERCIAL_PROPERTY_DETAILS_SCHEMA
        ]
        commercial_category = commercial_category_collection.find_one(
            {
                constants.INDEX_ID: ObjectId(
                    property_details.get(constants.PROPERTY_DETAILS_ID_FIELD)
                )
            }
        )
        if commercial_category is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Commercial Category Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response
        current_available_shares = commercial_category.get("carpet_area")

    elif property_details.get(constants.CATEGORY_FIELD) == "farm":
        farm_category_collection = db[constants.FARM_PROPERTY_DETAILS_SCHEMA]
        farm_category = farm_category_collection.find_one(
            {
                constants.INDEX_ID: ObjectId(
                    property_details.get(constants.PROPERTY_DETAILS_ID_FIELD)
                )
            }
        )
        if farm_category is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Farm Category Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response
        current_available_shares = farm_category.get("plot_area")
    else:
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_FAILURE,
            data={constants.MESSAGE: "Invalid Category"},
            status_code=HTTPStatus.NOT_FOUND,
        )
        return response
    response = ResponseMessage(
        type=constants.HTTP_RESPONSE_SUCCESS,
        data={"available_shares": current_available_shares},
        status_code=HTTPStatus.OK,
    )
    return response


def buy_investment_share(token, quantity, property_id):
    logger.debug("Inside Buy Investment Share Service")
    try:
        logger.debug("Decoding Token")
        decoded_token = token_decoder(token)
        user_id = decoded_token.get(constants.ID)
        logger.debug("Getting User Wallet for User: " + str(user_id))
        user_wallet_collection = db[constants.USER_WALLET_SCHEMA]
        customer_fiat_collection = db[constants.CUSTOMER_FIAT_TRANSACTIONS_SCHEMA]
        property_details_collection = db[constants.PROPERTY_DETAILS_SCHEMA]
        customer_transaction_details_collection = db[
            constants.CUSTOMER_TRANSACTION_SCHEMA
        ]
        user_wallet = user_wallet_collection.find_one({"user_id": user_id})
        if user_wallet is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "User Wallet Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response

        property_details = property_details_collection.find_one(
            {constants.INDEX_ID: ObjectId(property_id)}
        )

        if property_details is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Property Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response
        if property_details.get("available_shares") is None:
            fetch_available_shared_response = jsonable_encoder(
                fetch_available_shared(property_details)
            )
            if (
                fetch_available_shared_response.get("type")
                == constants.HTTP_RESPONSE_FAILURE
            ):
                return fetch_available_shared_response
            current_available_shares = fetch_available_shared_response.get("data").get(
                "available_shares"
            )
        else:
            current_available_shares = property_details.get("available_shares")

        current_price = property_details.get("price")
        if current_available_shares < quantity:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={
                    constants.MESSAGE: "Insufficient Shares, Please enter less quantity"
                },
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response

        if quantity * current_price > user_wallet.get("balance"):
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={
                    constants.MESSAGE: "Insufficient Balance, You need at least "
                    + str(quantity * current_price)
                    + " to buy "
                    + str(quantity)
                    + " shares"
                },
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response

        old_balance = user_wallet.get("balance")
        amount = quantity * current_price
        if user_wallet.get(property_id) is None:
            investment_value = quantity * current_price
            logger.debug("Property Not Exists in User Wallet")
            user_wallet[property_id] = jsonable_encoder(
                CustomerSharesInDb(
                    quantity=quantity,
                    avg_price=current_price,
                    investment_value=investment_value,
                )
            )
        else:
            logger.debug("Property Already Exists in User Wallet")
            user_wallet[property_id]["investment_value"] = user_wallet[property_id][
                "investment_value"
            ] + (quantity * current_price)
            user_wallet[property_id]["quantity"] = (
                user_wallet[property_id]["quantity"] + quantity
            )
            user_wallet[property_id]["avg_price"] = (
                user_wallet[property_id]["investment_value"]
                / user_wallet[property_id]["quantity"]
            )
            user_wallet[property_id]["updated_at"] = time.time()
        user_wallet["balance"] = user_wallet.get("balance") - (quantity * current_price)

        user_wallet_collection.update_one({"user_id": user_id}, {"$set": user_wallet})

        property_details_collection.update_one(
            {constants.INDEX_ID: ObjectId(property_id)},
            {"$set": {"available_shares": current_available_shares - quantity}},
        )

        customer_transaction_index = CustomerTransactionSchemaInDB(
            user_id=user_id,
            property_id=property_id,
            transaction_type="BUY",
            transaction_quantity=quantity,
            transaction_avg_price=current_price,
            transaction_amount=quantity * current_price,
            transaction_id=str(uuid4()),
            transaction_status="SUCCESS",
            transaction_date=time.time(),
            created_at=time.time(),
            updated_at=time.time()
        )

        transaction_index = customer_transaction_details_collection.insert_one(
            jsonable_encoder(customer_transaction_index)
        )

        fiat_record = jsonable_encoder(
            CustomerFiatTransactionSchemaInDB(
                user_id=user_id,
                balance=old_balance,
                transaction_amount=amount,
                transaction_id=str(uuid4()),
                transaction_type=TransactionType.BUY_SHARES.value,
                transaction_status="SUCCESS",
                transaction_date=time.time(),
            )
        )

        customer_fiat_collection.insert_one(fiat_record)
        body = f"{quantity} shares of {property_details.get('project_title')} bought successfully."
        customer_management_service.add_notifications("buy", "Buy", body, property_id,  token)

        del user_wallet["_id"]
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_SUCCESS,
            data={
                "user_wallet": user_wallet,
                "transaction_id": str(transaction_index.inserted_id),
            },
            status_code=HTTPStatus.OK,
        )
    except Exception as e:
        logger.error(f"Error in Buy Investment Share Service: {e}")
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_FAILURE,
            data={constants.MESSAGE: f"Error in Buy Investment Share Service: {e}"},
            status_code=e.status_code if hasattr(e, "status_code") else 500,
        )
    logger.debug("Returning From the Buy Investment Share Service")
    return response


def sell_investment_share(token, quantity, property_id):
    logger.debug("Inside Sell Investment Share Service")
    try:
        logger.debug("Decoding Token")
        decoded_token = token_decoder(token)
        user_id = decoded_token.get(constants.ID)
        logger.debug("Getting User Wallet for User: " + str(user_id))
        user_wallet_collection = db[constants.USER_WALLET_SCHEMA]
        property_details_collection = db[constants.PROPERTY_DETAILS_SCHEMA]
        customer_fiat_collection = db[constants.CUSTOMER_FIAT_TRANSACTIONS_SCHEMA]

        user_wallet = user_wallet_collection.find_one({"user_id": user_id})
        customer_transaction_details_collection = db[
            constants.CUSTOMER_TRANSACTION_SCHEMA
        ]
        if user_wallet is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "User Wallet Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response

        property_details = property_details_collection.find_one(
            {constants.INDEX_ID: ObjectId(property_id)}
        )

        if property_details is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Property Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response

        if user_wallet.get(property_id) is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "You do not have any shares of this property"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response

        if user_wallet.get(property_id).get("quantity") < quantity:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "You do not have enough shares to sell"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response

        current_available_shares = property_details.get("available_shares")

        current_price = property_details.get("price")

        old_balance = user_wallet.get("balance")
        amount = quantity * current_price

        user_wallet[property_id]["quantity"] = (
            user_wallet[property_id]["quantity"] - quantity
        )

        if user_wallet[property_id]["quantity"] == 0:
            user_wallet[property_id]["avg_price"] = 0
            user_wallet[property_id]["investment_value"] = 0
        else:
            user_wallet[property_id]["investment_value"] = (
                user_wallet[property_id]["quantity"]
                * user_wallet[property_id]["avg_price"]
            )
            user_wallet[property_id]["avg_price"] = (
                user_wallet[property_id]["investment_value"]
                / user_wallet[property_id]["quantity"]
            )

        user_wallet[property_id]["updated_at"] = time.time()

        user_wallet["balance"] = user_wallet.get("balance") + (quantity * current_price)

        user_wallet_collection.update_one({"user_id": user_id}, {"$set": user_wallet})

        property_details_collection.update_one(
            {constants.INDEX_ID: ObjectId(property_id)},
            {"$set": {"available_shares": current_available_shares + quantity}},
        )

        transaction_index = CustomerTransactionSchemaInDB(
            user_id=user_id,
            property_id=property_id,
            transaction_type="SELL",
            transaction_quantity=quantity,
            transaction_avg_price=current_price,
            transaction_amount=quantity * current_price,
            transaction_id=str(uuid4()),
            transaction_status="SUCCESS",
            transaction_date=time.time(),
            created_at=time.time(),
            updated_at=time.time()
        )

        customer_transaction_index = customer_transaction_details_collection.insert_one(
            jsonable_encoder(transaction_index)
        )

        fiat_record = jsonable_encoder(
            CustomerFiatTransactionSchemaInDB(
                user_id=user_id,
                balance=old_balance,
                transaction_amount=amount,
                transaction_id=str(uuid4()),
                transaction_type=TransactionType.SELL_SHARES.value,
                transaction_status="SUCCESS",
                transaction_date=time.time(),
                created_at=time.time(),
                updated_at=time.time()
            )
        )

        customer_fiat_collection.insert_one(fiat_record)
        body = f"{quantity} shares of {property_details.get('project_title')} sold successfully."
        customer_management_service.add_notifications("sell", "Sell", body, property_id,  token)

        del user_wallet["_id"]
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_SUCCESS,
            data={
                "user_wallet": user_wallet,
                "transaction_id": str(customer_transaction_index.inserted_id),
            },
            status_code=HTTPStatus.OK,
        )

    except Exception as e:
        logger.error(f"Error in Sell Investment Share Service: {e}")
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_FAILURE,
            data={constants.MESSAGE: f"Error in Sell Investment Share Service: {e}"},
            status_code=e.status_code if hasattr(e, "status_code") else 500,
        )

    logger.debug("Returning From the Sell Investment Share Service")
    return response


def get_transaction_details_by_id(token, transaction_id):
    logger.debug("Inside Get Transaction Details By Id Service")
    try:
        logger.debug("Decoding Token")
        decoded_token = token_decoder(token)
        user_id = decoded_token.get(constants.ID)
        customer_transaction_details_collection = db[
            constants.CUSTOMER_TRANSACTION_SCHEMA
        ]
        customer_transaction_details = customer_transaction_details_collection.find_one(
            {"transaction_id": (transaction_id)}
        )
        if customer_transaction_details is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Transaction Details Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response
        if customer_transaction_details.get("user_id") != user_id:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Transaction Details Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response
        del customer_transaction_details["_id"]
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_SUCCESS,
            data={"transaction_details": customer_transaction_details},
            status_code=HTTPStatus.OK,
        )
    except Exception as e:
        logger.error(f"Error in Get Transaction Details By Id Service: {e}")
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_FAILURE,
            data={
                constants.MESSAGE: f"Error in Get Transaction Details By Id Service: {e}"
            },
            status_code=e.status_code if hasattr(e, "status_code") else 500,
        )
    logger.debug("Returning From the Get Transaction Details By Id Service")
    return response


def get_customers_transactions(token):
    logger.debug("Inside Get Customers Transactions Service")
    try:
        logger.debug("Decoding Token")
        decoded_token = token_decoder(token)
        user_id = decoded_token.get(constants.ID)
        customer_transaction_details_collection = db[
            constants.CUSTOMER_TRANSACTION_SCHEMA
        ]
        customer_transaction_details = customer_transaction_details_collection.find(
            {"user_id": (user_id)},
            {
                "_id": 1,
                "property_id": 1,
                "transaction_type": 1,
                "transaction_amount": 1,
                "transaction_quantity": 1,
                "transaction_avg_price": 1,
                "transaction_id": 1,
                "transaction_status": 1,
                "transaction_date": 1,
            },
        )
        if customer_transaction_details is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Transaction Details Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response
        customer_transaction_details = list(customer_transaction_details)
        property_details_collection = db[constants.PROPERTY_DETAILS_SCHEMA]
        property_ids = [
            ObjectId(transaction.get("property_id"))
            for transaction in customer_transaction_details
        ]
        property_details = property_details_collection.find(
            {constants.INDEX_ID: {"$in": property_ids}},
            {"project_title": 1, "_id": 1},
        )

        property_dict = {}
        for property_detail in property_details:
            property_dict[
                str(property_detail.get(constants.INDEX_ID))
            ] = property_detail.get("project_title")

        transactions = []
        for transaction in customer_transaction_details:
            transaction["_id"] = str(transaction.get("_id"))
            transaction["property_title"] = property_dict.get(
                transaction.get("property_id")
            )
            transactions.append(transaction)
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_SUCCESS,
            data={"transactions": transactions},
            status_code=HTTPStatus.OK,
        )
    except Exception as e:
        logger.error(f"Error in Get Customers Transactions Service: {e}")
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_FAILURE,
            data={
                constants.MESSAGE: f"Error in Get Customers Transactions Service: {e}"
            },
            status_code=e.status_code if hasattr(e, "status_code") else 500,
        )
    logger.debug("Returning From the Get Customers Transactions Service")
    return response


def get_property_current_wallet_value(
    property_id: str,
    token: str,
):
    logger.debug("Inside Get Property Current Wallet Value By Id Service")
    try:
        logger.debug("Decoding Token")
        decoded_token = token_decoder(token)
        user_id = decoded_token.get(constants.ID)
        customer_wallet_collection = db[constants.USER_WALLET_SCHEMA]
        property_details_collection = db[constants.PROPERTY_DETAILS_SCHEMA]
        candle_details_collection = db[constants.CANDLE_DETAILS_SCHEMA]

        customer_wallet = customer_wallet_collection.find_one({"user_id": user_id})

        if customer_wallet is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Customer Wallet Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response

        property_details = property_details_collection.find_one(
            {constants.INDEX_ID: ObjectId(property_id)}
        )

        if property_details is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Property Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response

        candle_details = candle_details_collection.find_one(
            {constants.PROPERTY_ID_FIELD: property_id}
        )

        if candle_details is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Candle Details Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response

        if len(candle_details.get("candle_data")) <= 1:
            change_in_price = 0
        else:
            change_in_price = candle_details.get("candle_data")[-1].get(
                "price"
            ) - candle_details.get("candle_data")[-2].get("price")

        if property_details.get("available_shares") is None:
            fetch_available_shared_response = jsonable_encoder(
                fetch_available_shared(property_details)
            )
            if (
                fetch_available_shared_response.get("type")
                == constants.HTTP_RESPONSE_FAILURE
            ):
                return fetch_available_shared_response
            current_available_shares = fetch_available_shared_response.get("data").get(
                "available_shares"
            )
        else:
            current_available_shares = property_details.get("available_shares")

        response_dict = {
            "project_title": property_details.get("project_title"),
            "current_quantity": current_available_shares,
            "avg_price": property_details.get("price"),
            "current_price": property_details.get("price"),
            "change_in_price": change_in_price,
        }

        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_SUCCESS,
            data=response_dict,
            status_code=HTTPStatus.OK,
        )

    except Exception as e:
        logger.error(f"Error in Get Property Current Wallet Value Service: {e}")
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_FAILURE,
            data={
                constants.MESSAGE: f"Error in Get Property Current Wallet Value Service: {e}"
            },
            status_code=e.status_code if hasattr(e, "status_code") else 500,
        )

    logger.debug("Returning From the Get Property Current Wallet Value Service")
    return response


def get_investment_progress_details(
    property_id: str,
    token: str,
):
    logger.debug("Inside Get Investment Progress Details Service")
    try:
        logger.debug("Decoding Token")
        decoded_token = token_decoder(token)
        user_id = decoded_token.get(constants.ID)
        customer_wallet_collection = db[constants.USER_WALLET_SCHEMA]
        property_details_collection = db[constants.PROPERTY_DETAILS_SCHEMA]
        candle_details_collection = db[constants.CANDLE_DETAILS_SCHEMA]

        customer_wallet = customer_wallet_collection.find_one({"user_id": user_id})

        if customer_wallet is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Customer Wallet Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response

        property_wallet_info = customer_wallet.get(property_id)

        if property_wallet_info is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Property Not Found in Wallet"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response

        property_details = property_details_collection.find_one(
            {constants.INDEX_ID: ObjectId(property_id)}
        )

        if property_details is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Property Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response

        candle_details = candle_details_collection.find_one(
            {constants.PROPERTY_ID_FIELD: property_id}
        )

        if candle_details is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Candle Details Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response

        if len(candle_details.get("candle_data")) <= 1:
            total_return, one_day_return = 0, 0
            total_return_in_percent, one_day_return_in_percent = 0, 0
        else:
            one_day_return = candle_details.get("candle_data")[-1].get(
                "price"
            ) - candle_details.get("candle_data")[-2].get("price")
            one_day_return_in_percent = (
                one_day_return
                * 100
                / candle_details.get("candle_data")[-2].get("price")
            )
            total_return = candle_details.get("candle_data")[-1].get(
                "price"
            ) - candle_details.get("candle_data")[0].get("price")
            total_return_in_percent = (
                total_return * 100 / candle_details.get("candle_data")[0].get("price")
            )

        response_dict = {
            "total_return": total_return,
            "total_return_in_percent": total_return_in_percent,
            "1d_return": one_day_return,
            "1d_return_in_percent": one_day_return_in_percent,
            "current_value": property_details.get("price")
            * property_wallet_info.get("quantity"),
            "investment_value": property_wallet_info.get("investment_value"),
            "date_invested": property_wallet_info.get("created_at"),
        }

        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_SUCCESS,
            data=response_dict,
            status_code=HTTPStatus.OK,
        )

    except Exception as e:
        logger.error(f"Error in Get Investment Progress Details Service: {e}")
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_FAILURE,
            data={
                constants.MESSAGE: f"Error in Get Investment Progress Details Service: {e}"
            },
            status_code=e.status_code if hasattr(e, "status_code") else 500,
        )

    logger.debug("Returning From the Get Investment Progress Details Service")
    return response


def get_property_order_history(
    property_id: str, token: str, page_number: int, per_page: int
):
    logger.debug("Inside Get Property Order History Service")
    try:
        logger.debug("Decoding Token")
        decoded_token = token_decoder(token)
        user_id = decoded_token.get(constants.ID)
        customer_transaction_details_collection = db[
            constants.CUSTOMER_TRANSACTION_SCHEMA
        ]
        customer_transaction_details = (
            customer_transaction_details_collection.find(
                {"user_id": (user_id), "property_id": property_id},
            )
            .sort("transaction_date", -1)
            .skip((page_number - 1) * per_page)
            .limit(per_page)
        )
        if customer_transaction_details is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Transaction Details Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response
        customer_transaction_details = list(customer_transaction_details)
        property_details_collection = db[constants.PROPERTY_DETAILS_SCHEMA]
        property_details = property_details_collection.find_one(
            {constants.INDEX_ID: ObjectId(property_id)},
            {"project_title": 1, "_id": 1},
        )
        if property_details is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Property Details Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response
        transactions = []
        for transaction in customer_transaction_details:
            transaction["_id"] = str(transaction.get("_id"))
            transaction["property_title"] = property_details.get("project_title")
            transactions.append(transaction)

        count_total_transactions = (
            customer_transaction_details_collection.count_documents(
                {"user_id": (user_id), "property_id": property_id}
            )
        )
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_SUCCESS,
            data={
                "transactions": transactions,
                "total_transactions": count_total_transactions,
                "page_number": page_number,
                "per_page": per_page,
            },
            status_code=HTTPStatus.OK,
        )
    except Exception as e:
        logger.error(f"Error in Get Property Order History Service: {e}")
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_FAILURE,
            data={
                constants.MESSAGE: f"Error in Get Property Order History Service: {e}"
            },
            status_code=e.status_code if hasattr(e, "status_code") else 500,
        )
    logger.debug("Returning From the Get Property Order History Service")
    return response


def get_customer_fiat_transactions(page_number: int, per_page: int, token: str):
    logger.debug("Inside Get Customer Fiat Transaction Service")
    try:
        logger.debug("Decoding Token")
        decoded_token = token_decoder(token)
        user_id = decoded_token.get(constants.ID)
        customer_fiat_transaction_details_collection = db[
            constants.CUSTOMER_FIAT_TRANSACTIONS_SCHEMA
        ]
        customer_transaction_details = (
            customer_fiat_transaction_details_collection.find(
                {"user_id": (user_id)},
            )
            .sort("transaction_date", -1)
            .skip((page_number - 1) * per_page)
            .limit(per_page)
        )
        if customer_transaction_details is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Transaction Details Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response
        transactions = []
        for transaction in customer_transaction_details:
            transaction["_id"] = str(transaction.get("_id"))
            transactions.append(transaction)

        count_total_transactions = (
            customer_fiat_transaction_details_collection.count_documents(
                {"user_id": (user_id)}
            )
        )
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_SUCCESS,
            data={
                "transactions": transactions,
                "total_transactions": count_total_transactions,
                "page_number": page_number,
                "per_page": per_page,
            },
            status_code=HTTPStatus.OK,
        )

    except Exception as e:
        logger.error(f"Error in Get Customer Fiat Transaction Service: {e}")
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_FAILURE,
            data={
                constants.MESSAGE: f"Error in Get Customer Fiat Transaction Service: {e}"
            },
            status_code=e.status_code if hasattr(e, "status_code") else 500,
        )
    logger.debug("Returning From the Get Customer Fiat Transaction Service")
    return response


def get_fiat_transaction_details_by_id(token, transaction_id):
    logger.debug("Inside Get Transaction Details By Id Service")
    try:
        logger.debug("Decoding Token")
        decoded_token = token_decoder(token)
        user_id = decoded_token.get(constants.ID)
        customer_transaction_details_collection = db[
            constants.CUSTOMER_FIAT_TRANSACTIONS_SCHEMA
        ]
        customer_transaction_details = customer_transaction_details_collection.find_one(
            {"transaction_id": (transaction_id)}
        )
        if customer_transaction_details is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Transaction Details Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response
        if customer_transaction_details.get("user_id") != user_id:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Transaction Details Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response
        del customer_transaction_details["_id"]
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_SUCCESS,
            data={"transaction_details": customer_transaction_details},
            status_code=HTTPStatus.OK,
        )
    except Exception as e:
        logger.error(f"Error in Get Transaction Details By Id Service: {e}")
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_FAILURE,
            data={
                constants.MESSAGE: f"Error in Get Transaction Details By Id Service: {e}"
            },
            status_code=e.status_code if hasattr(e, "status_code") else 500,
        )
    logger.debug("Returning From the Get Transaction Details By Id Service")
    return response


def shares_graph(property_id: str):
    try:
        logger.debug("Decoding Token")
        property_details_collection = db[constants.PROPERTY_DETAILS_SCHEMA]

        property_details = property_details_collection.find_one(
            {constants.INDEX_ID: ObjectId(property_id)}
        )

        if property_details is None:
            response = ResponseMessage(
                type=constants.HTTP_RESPONSE_FAILURE,
                data={constants.MESSAGE: "Property Not Found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
            return response
        current_shares = ""
        fetch_available_shared_response = jsonable_encoder(
            fetch_available_shared(property_details)
        )
        if (
            fetch_available_shared_response.get("type")
            == constants.HTTP_RESPONSE_FAILURE
        ):
            return fetch_available_shared_response
        total_shares = fetch_available_shared_response.get("data").get(
            "available_shares"
        )
        current_available_shares = property_details.get("available_shares")
        response_dict = {
            "current_available_shares": current_available_shares
            if current_available_shares
            else 0,
            "total_shares": total_shares,
        }

        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_SUCCESS,
            data=response_dict,
            status_code=HTTPStatus.OK,
        )

    except Exception as e:
        logger.error(f"Error in Get Transaction Details By Id Service: {e}")
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_FAILURE,
            data={
                constants.MESSAGE: f"Error in Get Transaction Details By Id Service: {e}"
            },
            status_code=e.status_code if hasattr(e, "status_code") else 500,
        )
    logger.debug("Returning From the Get Transaction Details By Id Service")
    return response


def get_filtered_fiat_transactions(
    min_transaction_date,
    max_transaction_date,
    transaction_type,
    transaction_id,
    page_number,
    per_page,
    token,
):
    try:
        logger.debug("Decoding Token")
        decoded_token = token_decoder(token)
        user_id = decoded_token.get(constants.ID)
        customer_transaction_details_collection = db[
            constants.CUSTOMER_FIAT_TRANSACTIONS_SCHEMA
        ]

        skip = (page_number - 1) * per_page

        filter_query = {constants.USER_ID_FIELD: user_id}

        # Iterate through filter parameters and add to the query if not None
        filter_query = {
            key: value
            for key, value in {
                "transaction_type": transaction_type,
                "transaction_id": transaction_id,
            }.items()
            if value is not None
        }

        if min_transaction_date and max_transaction_date:
            filter_query["transaction_date"] = {
                "$gte": min_transaction_date,
                "$lte": max_transaction_date,
            }
        elif min_transaction_date:
            filter_query["transaction_date"] = {
                "$gte": min_transaction_date,
            }
        elif max_transaction_date:
            filter_query["transaction_date"] = {"$lte": max_transaction_date}
        # Query the MongoDB collection with the filter query and apply pagination
        filtered_transactions = list(
            customer_transaction_details_collection.find(filter_query)
            .sort("transaction_date", -1)
            .skip(skip)
            .limit(per_page)
        )

        response_data = []

        for record in filtered_transactions:
            record[constants.ID] = str(record[constants.INDEX_ID])
            del record[constants.INDEX_ID]
            response_data.append(record)
        document_count = customer_transaction_details_collection.count_documents(
            filter_query
        )
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_SUCCESS,
            data={
                "response_data": response_data,
                "page_number": page_number,
                "per_page": per_page,
                "document_count": document_count,
            },
            status_code=HTTPStatus.OK,
        )

    except Exception as e:
        logger.error(f"Error in Get Fiat Transaction Filter Service: {e}")
        response = ResponseMessage(
            type=constants.HTTP_RESPONSE_FAILURE,
            data={
                constants.MESSAGE: f"Error in Get Fiat Transaction Filter Service: {e}"
            },
            status_code=e.status_code if hasattr(e, "status_code") else 500,
        )
    logger.debug("Returning From the Get Fiat Transaction Filter Service")
    return response


def user_wallet_snapshot_handler():
    try:
        customer_collection = db[constants.USER_DETAILS_SCHEMA]
        customer_wallet_collection = db[constants.USER_WALLET_SCHEMA]
        property_details_collection = db[constants.PROPERTY_DETAILS_SCHEMA]
        portfolio_analysis_collection = db[constants.PORTFOLIO_ANALYSIS_SCHEMA]
        customer_ids = list(
            customer_collection.find(
                {
                    constants.IS_ACTIVE_FIELD: True,
                    constants.USER_TYPE_FIELD: {
                        constants.IN_OPERATOR: ["customer", "partner"]
                    },
                },
                {constants.INDEX_ID: 1},
            )
        )
        customer_ids = list(map(lambda x: str(x.get(constants.INDEX_ID)), customer_ids))
        customer_wallets = list(
            customer_wallet_collection.find(
                {constants.USER_ID_FIELD: {constants.IN_OPERATOR: customer_ids}}
            )
        )
        property_price_dict = {}
        for wallet in customer_wallets:
            wallet_property_id = []
            for property_id in wallet.keys():
                if property_id in [
                    "_id",
                    "user_id",
                    "balance",
                    "updated_at",
                    "created_at",
                ]:
                    continue
                else:
                    wallet_property_id.append(ObjectId(property_id))

            # Get all the wallet properties
            filtered_property_id = list(
                filter(lambda x: str(x) not in property_price_dict, wallet_property_id)
            )
            property_details = list(
                property_details_collection.find(
                    {constants.INDEX_ID: {constants.IN_OPERATOR: filtered_property_id}},
                    {constants.INDEX_ID: 1, constants.PRICE_FIELD: 1},
                )
            )
            for property_data in property_details:
                property_price_dict[
                    str(property_data[constants.INDEX_ID])
                ] = property_data[constants.PRICE_FIELD]

            record_data = []
            for record in wallet_property_id:
                record_dict = {
                    constants.PRICE_FIELD: property_price_dict.get(str(record)),
                    constants.QUANTITY_FIELD: wallet.get(str(record)).get(
                        constants.QUANTITY_FIELD
                    ),
                }
                record_data.append({str(record): record_dict})
            portfolio_analysis = portfolio_analysis_collection.find_one(
                {constants.USER_ID_FIELD: wallet.get(constants.USER_ID_FIELD)}
            )
            if portfolio_analysis:
                portfolio_data = {constants.UPDATED_AT_FIELD: time.time()}
                if record_data:
                    for item in record_data:
                        portfolio_data.update(item)
                portfolio_analysis_collection.find_one_and_update(
                    {constants.USER_ID_FIELD: wallet.get(constants.USER_ID_FIELD)},
                    {constants.UPDATE_INDEX_DATA: portfolio_data},
                )
            else:
                portfolio_data = {
                    constants.USER_ID_FIELD: wallet.get(constants.USER_ID_FIELD),
                    constants.CREATED_AT_FIELD: time.time(),
                    constants.UPDATED_AT_FIELD: time.time(),
                }
                if record_data:
                    for item in record_data:
                        portfolio_data.update(item)
                    portfolio_analysis_collection.insert_one(portfolio_data)
        logger.debug("User wallets Snapshot taken Successfully")

    except Exception as e:
        logger.error(f"Error in Get Fiat Transaction Filter Service: {e}")
