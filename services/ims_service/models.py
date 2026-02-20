from typing import List, Optional
from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str
    name: str
    is_developer: bool = False
    is_active: bool = True


class UpdateUserRequest(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    name: Optional[str] = None
    is_developer: Optional[bool] = None
    is_active: Optional[bool] = None


class CompanyInfo(BaseModel):
    code: str
    name: str
    role: str


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    is_developer: bool
    companies: List[CompanyInfo]
    access_token: str
    token_type: str = "bearer"
