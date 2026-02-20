from datetime import datetime, timedelta

import bcrypt
from jose import jwt
from sqlalchemy import text
from sqlalchemy.orm import Session

from shared.config_loader import settings
from shared.logger import get_logger

logger = get_logger("ims.tools")


def _create_access_token(user_id: str, email: str) -> str:
    payload = {
        "user_id": user_id,
        "email": email,
        "exp": datetime.utcnow() + timedelta(hours=settings.IMS_JWT_EXPIRATION_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, settings.IMS_JWT_SECRET, algorithm=settings.IMS_JWT_ALGORITHM)


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def create_user(
    email: str, password: str, name: str, is_developer: bool, is_active: bool, db: Session
) -> dict | None:
    existing = db.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": email},
    ).mappings().first()

    if existing:
        return None  # email conflict

    password_hash = _hash_password(password)

    row = db.execute(
        text("""
            INSERT INTO users (email, name, password_hash, is_developer, is_active)
            VALUES (:email, :name, :password_hash, :is_developer, :is_active)
            RETURNING id, email, name, is_developer, is_active
        """),
        {
            "email": email,
            "name": name,
            "password_hash": password_hash,
            "is_developer": is_developer,
            "is_active": is_active,
        },
    ).mappings().first()

    logger.info(f"Created user: {row['id']} ({email})")

    return {
        "id": str(row["id"]),
        "email": row["email"],
        "name": row["name"],
        "is_developer": row["is_developer"],
        "is_active": row["is_active"],
    }


def update_user(user_id: str, updates: dict, db: Session) -> dict | None:
    existing = db.execute(
        text("SELECT id FROM users WHERE id = :user_id"),
        {"user_id": user_id},
    ).mappings().first()

    if not existing:
        return None

    if "password" in updates:
        updates["password_hash"] = _hash_password(updates.pop("password"))

    if "email" in updates:
        conflict = db.execute(
            text("SELECT id FROM users WHERE email = :email AND id != :user_id"),
            {"email": updates["email"], "user_id": user_id},
        ).mappings().first()
        if conflict:
            return "email_conflict"

    if not updates:
        return "no_fields"

    set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
    updates["user_id"] = user_id

    row = db.execute(
        text(f"""
            UPDATE users SET {set_clauses}
            WHERE id = :user_id
            RETURNING id, email, name, is_developer, is_active
        """),
        updates,
    ).mappings().first()

    logger.info(f"Updated user: {user_id}")

    return {
        "id": str(row["id"]),
        "email": row["email"],
        "name": row["name"],
        "is_developer": row["is_developer"],
        "is_active": row["is_active"],
    }


def list_users(db: Session) -> list[dict]:
    rows = db.execute(
        text("SELECT id, email, name, is_developer, is_active FROM users ORDER BY name ASC")
    ).mappings().all()

    return [
        {
            "id": str(r["id"]),
            "email": r["email"],
            "name": r["name"],
            "is_developer": r["is_developer"],
            "is_active": r["is_active"],
        }
        for r in rows
    ]


def delete_user(email: str, db: Session) -> bool:
    row = db.execute(
        text("DELETE FROM users WHERE email = :email RETURNING id"),
        {"email": email},
    ).mappings().first()

    if not row:
        return False

    logger.info(f"Deleted user: {email}")
    return True


def login(email: str, password: str, db: Session) -> dict:
    row = db.execute(
        text("""
            SELECT id, email, name, password_hash, is_developer, is_active
            FROM users
            WHERE email = :email AND is_active = true
        """),
        {"email": email},
    ).mappings().first()

    if not row:
        logger.warning(f"Login failed — email not found: {email}")
        return None

    if not row["password_hash"] or not bcrypt.checkpw(
        password.encode("utf-8"), row["password_hash"].encode("utf-8")
    ):
        logger.warning(f"Login failed — wrong password: {email}")
        return None

    user_id = str(row["id"])
    companies = get_user_companies(user_id, db)
    access_token = _create_access_token(user_id, row["email"])

    logger.info(f"Login successful: {user_id} ({email})")

    return {
        "id": user_id,
        "email": row["email"],
        "name": row["name"],
        "is_developer": row["is_developer"],
        "companies": companies,
        "access_token": access_token,
        "token_type": "bearer",
    }


def get_user_companies(user_id: str, db: Session) -> list[dict]:
    rows = db.execute(
        text("""
            SELECT c.code, c.name, ucr.role
            FROM user_company_roles ucr
            JOIN companies c ON ucr.company_code = c.code
            WHERE ucr.user_id = :user_id AND c.is_active = true
            ORDER BY
                CASE ucr.role
                    WHEN 'developer' THEN 6
                    WHEN 'admin'     THEN 5
                    WHEN 'ops'       THEN 4
                    WHEN 'approver'  THEN 3
                    WHEN 'viewer'    THEN 2
                    ELSE 1
                END DESC,
                c.code ASC
        """),
        {"user_id": user_id},
    ).mappings().all()

    return [{"code": r["code"], "name": r["name"], "role": r["role"]} for r in rows]


def get_dashboard_info(user_id: str, company_code: str, db: Session) -> dict | None:
    # Verify company access
    company = db.execute(
        text("""
            SELECT c.code, c.name, ucr.role
            FROM user_company_roles ucr
            JOIN companies c ON ucr.company_code = c.code
            WHERE ucr.user_id = :user_id
              AND c.code = :company_code
              AND c.is_active = true
        """),
        {"user_id": user_id, "company_code": company_code},
    ).mappings().first()

    if not company:
        return None

    # Fetch module permissions
    modules = db.execute(
        text("""
            SELECT
                m.code   AS module_code,
                m.name   AS module_name,
                COALESCE(mp.can_access,  false) AS can_access,
                COALESCE(mp.can_view,    false) AS can_view,
                COALESCE(mp.can_create,  false) AS can_create,
                COALESCE(mp.can_edit,    false) AS can_edit,
                COALESCE(mp.can_delete,  false) AS can_delete,
                COALESCE(mp.can_approve, false) AS can_approve
            FROM modules m
            LEFT JOIN module_permissions mp
                ON m.code = mp.module_code
               AND mp.user_id = :user_id
               AND mp.company_code = :company_code
            WHERE m.company_code = :company_code AND m.is_active = true
            ORDER BY m.order_index, m.code
        """),
        {"user_id": user_id, "company_code": company_code},
    ).mappings().all()

    # Dashboard stats
    stats = db.execute(
        text("""
            SELECT
                COUNT(*) AS total_modules,
                SUM(CASE WHEN mp.can_access = true THEN 1 ELSE 0 END) AS accessible_modules
            FROM modules m
            LEFT JOIN module_permissions mp
                ON m.code = mp.module_code
               AND mp.user_id = :user_id
               AND mp.company_code = :company_code
            WHERE m.company_code = :company_code AND m.is_active = true
        """),
        {"user_id": user_id, "company_code": company_code},
    ).mappings().first()

    module_list = [
        {
            "module_code": m["module_code"],
            "module_name": m["module_name"],
            "permissions": {
                "access": m["can_access"],
                "view": m["can_view"],
                "create": m["can_create"],
                "edit": m["can_edit"],
                "delete": m["can_delete"],
                "approve": m["can_approve"],
            },
        }
        for m in modules
    ]

    return {
        "company": {
            "code": company["code"],
            "name": company["name"],
            "role": company["role"],
        },
        "dashboard": {
            "stats": {
                "total_modules": stats["total_modules"],
                "accessible_modules": int(stats["accessible_modules"] or 0),
            },
            "permissions": {
                "modules": module_list,
            },
        },
    }


def get_current_user(user_id: str, db: Session) -> dict | None:
    row = db.execute(
        text("""
            SELECT id, email, name, is_developer
            FROM users
            WHERE id = :user_id AND is_active = true
        """),
        {"user_id": user_id},
    ).mappings().first()

    if not row:
        return None

    companies = get_user_companies(user_id, db)

    return {
        "id": str(row["id"]),
        "email": row["email"],
        "name": row["name"],
        "is_developer": row["is_developer"],
        "companies": companies,
    }


def check_permission(
    user_id: str, company_code: str, module_code: str, action: str, db: Session
) -> dict:
    row = db.execute(
        text("""
            SELECT
                CASE :action
                    WHEN 'access'  THEN mp.can_access
                    WHEN 'view'    THEN mp.can_view
                    WHEN 'create'  THEN mp.can_create
                    WHEN 'edit'    THEN mp.can_edit
                    WHEN 'delete'  THEN mp.can_delete
                    WHEN 'approve' THEN mp.can_approve
                    ELSE false
                END AS has_permission
            FROM module_permissions mp
            WHERE mp.user_id = :user_id
              AND mp.company_code = :company_code
              AND mp.module_code = :module_code
        """),
        {
            "user_id": user_id,
            "company_code": company_code,
            "module_code": module_code,
            "action": action,
        },
    ).mappings().first()

    return {
        "has_permission": bool(row["has_permission"]) if row else False,
        "user_id": user_id,
        "company": company_code,
        "module": module_code,
        "action": action,
    }
