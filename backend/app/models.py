"""
app/models.py

Single source of truth for ALL Pydantic models in the project.

Sections:
  1. FAQ / RAG models       — document schema (used by RAGEngine)
  2. API request / response — chat endpoint contracts
  3. Tool input schemas     — args_schema for every @tool decorator
  4. Tool sub-models        — nested structures (ReturnItem, ExchangeItem)
"""

from __future__ import annotations

from typing import List, Literal, Optional
from pydantic import BaseModel, Field, EmailStr, field_validator
from enum import StrEnum

from app.utils.validators import validate_session_id


# ===========================================================================
# 1. FAQ / RAG models
# ===========================================================================

class FAQMetadata(BaseModel):
    category: str
    action_type: Literal["static_faq", "tool_call"]
    source: str
    updated_at: str


class FAQItem(BaseModel):
    id: str
    category: str
    action_type: Literal["static_faq", "tool_call"]
    tool_name: Optional[str] = None  # Only for action_type "tool_call"
    question: str
    answer: str
    keywords: List[str]
    embedding_text: str
    metadata: FAQMetadata


# ===========================================================================
# 2. API request / response models
# ===========================================================================

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)
    session_id: str = Field(default="default")
    category_filter: Optional[str] = Field(
        default=None,
        description="Filter knowledge base search to a specific FAQ category.",
    )
    image_base64: Optional[str] = Field(default=None, max_length=15_000_000)

    @field_validator("session_id")
    @classmethod
    def validate_session(cls, value: str) -> str:
        if value == "default":
            raise ValueError("session_id must be registered via POST /chat/user before chatting")
        return validate_session_id(value)



class ChatResponse(BaseModel):
    """
    Single unified response shape.
    tool_calls lists every tool the agent invoked, including search_knowledge_base.
    """
    session_id: str
    answer: str
    tool_calls: List[str] = []


class IndexResponse(BaseModel):
    status: str
    total_documents: int
    categories: List[str]


class HealthResponse(BaseModel):
    status: str
    index_loaded: bool
    total_docs: int


# ===========================================================================
# 3. Tool input schemas
# ===========================================================================

class KnowledgeBaseInput(BaseModel):
    """Input for the search_knowledge_base tool."""
    query: str = Field(..., description="The customer's question to search the FAQ knowledge base.")
    category_filter: Optional[str] = Field(
        None,
        description="Optional FAQ category to restrict search, e.g. 'shipping', 'returns'.",
    )

class ProductListInput(BaseModel):
    department: str = Field(..., description="Filter by department. Accepted values are: Women, Men, Girls, and Boys")

class CategoryListInput(BaseModel):
    department: str = Field(..., description="Filter by department. Accepted values are: Women, Men, Girls, and Boys")


class OrderLookupInput(BaseModel):
    order_id: int = Field(..., description="Unique order identifier, e.g. '6076088850'.")
    @field_validator("order_id")
    @classmethod
    def validate_order_id(cls, value: int) -> int:
        if len(str(value)) != 10:
            raise ValueError("order_id must be exactly 10 digits")
        return value

class IncidentOrderInput(BaseModel):
    email: EmailStr = Field(..., description="Customer email address provided during order placement.")
    
    

class CancelOrderInput(BaseModel):
    order_id: int = Field(..., description="Unique order identifier.")
    email: EmailStr = Field(..., description="Customer email address provided during order placement.")
    cancel_reason: str = Field(..., description="Reason for cancellation.")
    @field_validator("order_id")
    @classmethod
    def validate_order_id(cls, value: int) -> int:
        if len(str(value)) != 10:
            raise ValueError("order_id must be exactly 10 digits")
        return value
    

class OrderAndEmailInput(BaseModel):
    order_id: int = Field(..., description="Unique order identifier.")
    email: EmailStr = Field(..., description="Customer email address provided during order placement.")


class UpdateShippingInput(BaseModel):
    order_id: int = Field(..., description="Unique order identifier.")
    email: EmailStr = Field(..., description="Customer email address provided during order placement for verification.")
    first_name: Optional[str] = Field(None, description="First name on the shipping label.")
    last_name: Optional[str] = Field(None, description="Last name on the shipping label.")
    phone: Optional[str] = Field(None, description="Contact phone for the shipment.")
    address_line_1: Optional[str] = Field(None, description="Primary street address.")
    address_line_2: Optional[str] = Field(None, description="Apartment, suite, floor, etc.")
    city: Optional[str] = Field(None, description="City name.")
    post_code: Optional[str] = Field(None, description="Postal / ZIP code.")


class ValidateDiscountInput(BaseModel):
    coupon_code: str = Field(..., description="Coupon or discount code to validate.")


class CreateReturnInput(BaseModel):
    order_id: int = Field(..., description="Unique order identifier.")
    email: EmailStr = Field(..., description="Customer email address for verification.")
    items: List[ReturnItem] = Field(..., min_length=1, description="Items to return.")


class ReturnStatusInput(BaseModel):
    return_request_id: str = Field(..., description="The unique identifier of the return request. This value is generated and returned by the server only after `create_return_request` has been successfully executed.")


class SendInvoiceInput(BaseModel):
    order_id: int = Field(..., description="Unique order identifier.")



class SupportTicketCategory(StrEnum):
    ACCOUNT_DELETE = "account_delete"
    ACCOUNT_REACTIVATE = "account_reactivate"
    ORDER_CANCEL_REQUEST = "order_cancel_request"
    ORDER_NOT_PLACED = "order_not_placed"
    LOST_OR_DAMAGED = "lost_or_damaged"
    DELIVERY_FAILED = "delivery_failed"
    DELIVERY_DELAY = "delivery_delay"
    PRODUCT_RESTOCK_REQUEST = "product_restock_request"
    CUSTOMIZE_PRODUCT_ORDER_REQUEST = "customize_product_order_request"
    B2B_WHOLESALE = "b2b_wholesale"
    UNWANTED_COMMUNICATION = "unwanted_communication"
    WRONG_RECIPIENT_NOTIFICATION = "wrong_recipient_notification"
    RESHIPMENT_REQUEST = "reshipment_request"
    LIVE_SUPPORT_REQUEST = "live_support_request"
    CUSTOMER_DISSATISFACTION = "customer_dissatisfaction"
    WEBSITE_BUG_OR_TECHNICAL_ISSUE_REPORT = "website_bug_or_technical_issue_report"
    AFFILIATE_PARTNERSHIP_REQUEST = "affiliate_partnership_request"
    GENERAL_SUPPORT = "general_support"


CATEGORY_DESCRIPTIONS = {
    SupportTicketCategory.ACCOUNT_DELETE:
        "Customer wants to permanently delete their account.",

    SupportTicketCategory.ACCOUNT_REACTIVATE:
        "Customer wants to restore a deleted or deactivated account.",

    SupportTicketCategory.ORDER_CANCEL_REQUEST:
        "Customer wants to cancel an existing order.",

    SupportTicketCategory.ORDER_NOT_PLACED:
        "Customer paid but no order exists in the system.",

    SupportTicketCategory.LOST_OR_DAMAGED:
        "Shipment was lost or arrived damaged.",

    SupportTicketCategory.DELIVERY_FAILED:
        "Courier failed delivery or package was returned to sender.",

    SupportTicketCategory.DELIVERY_DELAY:
        "Order has not arrived within expected timeframe.",

    SupportTicketCategory.PRODUCT_RESTOCK_REQUEST:
        "Customer wants an out-of-stock item restocked.",

    SupportTicketCategory.CUSTOMIZE_PRODUCT_ORDER_REQUEST:
        "Customer wants a custom or personalized product.",

    SupportTicketCategory.B2B_WHOLESALE:
        "Wholesale, reseller, bulk purchase, or business inquiry.",

    SupportTicketCategory.UNWANTED_COMMUNICATION:
        "Customer received unwanted emails, SMS, or notifications.",

    SupportTicketCategory.WRONG_RECIPIENT_NOTIFICATION:
        "Customer received notifications intended for another person.",

    SupportTicketCategory.RESHIPMENT_REQUEST:
        "Customer wants an order shipped again.",

    SupportTicketCategory.LIVE_SUPPORT_REQUEST:
        "Customer wants a human agent or callback.",

    SupportTicketCategory.CUSTOMER_DISSATISFACTION:
        "Customer remains unhappy despite resolution or interaction.",

    SupportTicketCategory.WEBSITE_BUG_OR_TECHNICAL_ISSUE_REPORT:
        "Website, app, login, checkout, payment, or technical issue.",

    SupportTicketCategory.AFFILIATE_PARTNERSHIP_REQUEST:
        "Affiliate, influencer, sponsorship, or partnership inquiry.",

    SupportTicketCategory.GENERAL_SUPPORT:
        "Fallback category when nothing else clearly applies.",
}


class CreateSupportTicketInput(BaseModel):
    name: str = Field(..., description="Customer's full name.")
    email: EmailStr = Field(..., description="Customer's email address.")
    phone: str = Field(..., description="Customer's phone number.")
    category: SupportTicketCategory = Field(
        ..., 
        description="""
        Support ticket category.

        You MUST:
        1. Understand the user's problem first
        2. Detect intent from the full message
        3. Select EXACTLY ONE category from the allowed list        
        """
    )
    subject: str = Field(..., description="Short summary of the issue. write by llm.")
    message: str = Field(..., description="Full description of the issue.")
    order_id: Optional[str] = Field(None, description="Related order ID (optional).")


class GetOrderForExchangeInput(BaseModel):
    order_number: int = Field(..., description="Order number to check exchange eligibility.")


class SubmitExchangeInput(BaseModel):
    order_number: str = Field(..., description="Order number being exchanged.")
    exchangeable_items: List[ExchangeItem] = Field(..., min_length=1)
    comment: Optional[str] = Field(None, description="Optional overall comment.")


# ===========================================================================
# 4. Tool sub-models
# ===========================================================================

class ReturnItem(BaseModel):
    ordered_product_unique_id: int = Field(..., description="ordered_product_unique_id of the order-product row from get_order_details tools.")
    quantity: int = Field(..., ge=1, description="Units to return.")
    return_reasons: str = Field(..., description="Customer's reason.")


class ExchangeItem(BaseModel):
    product_id: int = Field(..., description="Product ID to exchange into.")
    size_name: str = Field(..., description="Desired size, e.g. 'M', 'XL'.")
    color_name: str = Field(..., description="Desired colour, e.g. 'Black'.")
    quantity: int = Field(..., ge=1, description="Units to exchange.")
    reason: Optional[str] = Field(None, description="Optional per-item reason.")


# ===========================================================================
# 5. Chat history models 
# ===========================================================================

class ChatUser(BaseModel):
    id: int
    name: str
    email: str
    session_id: str

class ChatUserRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr


class ChatHistoryMessage(BaseModel):
    role: str
    message: str
    timestamp: str
    image_path: Optional[str] = None


class ChatHistoryPaginationMeta(BaseModel):
    total_items: int
    total_pages: int
    current_page: int
    page_size: int


class ChatHistoryResponse(BaseModel):
    user: Optional[ChatUser] = None
    data: List[ChatHistoryMessage]
    pagination: ChatHistoryPaginationMeta



# ===========================================================================
# 6. Product rag models  
# ===========================================================================

class ProductSearchInput(BaseModel):
    """Input schema for the search_products tool."""
    query: str = Field(
        description=(
            "Natural-language product search query. "
            "Examples: 'red summer dress', 'black bodysuit for going out', "
            "'affordable party tops under £30'."
        )
    )
    department: Optional[str] = Field(
        description="Filter by department. Accepted values are: women, men, girls, and boys."
    )
    category: str = Field(
        description=(
            "Find best matches category from 'find_product_category' tool. after understanding intent. and provide category name."
        )
    )
    color: Optional[str] = Field(
        default=None,
        description="Specific colour the customer wants, e.g. 'Black', 'Red'."
    )
    size: Optional[str] = Field(
        default=None,
        description="UK clothing size, e.g. '8', '12', '16'. Leave None if not mentioned."
    )
    occasion: Optional[str] = Field(
        default=None,
        description=(
            "Occasion or use-case filter, e.g. 'Party Wear', 'Casual', 'Holiday', "
            "'Going Out', 'Activewear'."
        )
    )
    min_price: Optional[float] = Field(
        default=None,
        description="Minimum price in GBP. Infer from phrases like 'over £20'."
    )
    max_price: Optional[float] = Field(
        default=None,
        description="Maximum price in GBP. Infer from phrases like 'under £40', 'cheap', 'affordable'."
    )
    in_stock_only: bool = Field(
        default=True,
        description="Return only in-stock products. Default True."
    )
    top_k: int = Field(
        default=5,
        description="Number of products to return (1–10). Default 5.",
        ge=1,
        le=10,
    )




# ===========================================================================
# 7. Product Image RAG models  
# ===========================================================================

class ImageIndexResponse(BaseModel):
    status: str
    total_products: int
    failed: int
    failed_details: list = []

class ImageSearchResult(BaseModel):
    rank: int
    score: float
    product_id: str
    product_name: Optional[str] = None
    product_url: Optional[str] = None
    product_image: Optional[str] = None
    department: Optional[str] = None
    category: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    discount_price: Optional[float | int] = None
    discount_percent: Optional[float | int] = None
    has_discount: Optional[bool] = None
    in_stock: Optional[bool] = None
    rating: Optional[float | int] = None
    total_reviews: Optional[int] = None
    colors: Optional[list] = None
    sizes: Optional[list] = None
    fabric: Optional[str] = None
    fit: Optional[str] = None
    sleeve: Optional[str] = None
    neckline: Optional[str] = None
    season: Optional[str] = None
    occasion: Optional[list] = None

class ImageSearchResponse(BaseModel):
    top_k: int
    results: list[ImageSearchResult]


class ImageSearchB64Request(BaseModel):
    image_base64: str
    top_k: Optional[int] = None


# ===========================================================================
# 8. Admin / handoff / analytics models
# ===========================================================================

class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)


class AdminRefreshRequest(BaseModel):
    refresh_token: str


class AdminUserResponse(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_online: Optional[int] = 0
    last_seen_at: Optional[str] = None
    created_at: Optional[str] = None


class AdminTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: AdminUserResponse


class HandoffRequestBody(BaseModel):
    session_id: str
    reason: Optional[str] = None


class HandoffMessageBody(BaseModel):
    session_id: str
    message: str = Field(..., min_length=1, max_length=2000)


class AgentMessageBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


class AgentPresenceBody(BaseModel):
    is_online: bool


class FeedbackRequest(BaseModel):
    session_id: str
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = Field(default=None, max_length=1000)


class ConversationTagsBody(BaseModel):
    tags: List[str]


class ConversationExportBody(BaseModel):
    conversation_ids: Optional[List[int]] = None


class BusinessHoursBody(BaseModel):
    enabled: bool = True
    timezone: str = "Europe/London"
    days: dict
    offline_message: str = ""


class InquiryStatusBody(BaseModel):
    status: str = Field(..., pattern="^(pending|in_progress|resolved|rejected)$")
    note: Optional[str] = Field(default=None, max_length=2000)


# Resolve forward references
CreateReturnInput.model_rebuild()
SubmitExchangeInput.model_rebuild()