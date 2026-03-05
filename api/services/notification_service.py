from sqlalchemy.orm import Session
from datetime import datetime
from db.pg_models import UserNotification, NotificationType, NotificationHistory
from api.routes.customer_service import notification_manager
import json
import logging

logger = logging.getLogger(__name__)

class NotificationService:
    @staticmethod
    def create_notification(
        db: Session,
        user_id: int,
        type: str,
        title: str,
        message: str,
        link: str = None,
        track_history: bool = True
    ):
        """
        Create a new notification and notify user via WebSocket if connected.
        Also tracks history to prevent spam if track_history is True.
        """
        try:
            notification = UserNotification(
                user_id=user_id,
                type=type,
                title=title,
                message=message,
                link=link,
                created_at=datetime.utcnow()
            )
            db.add(notification)
            
            if track_history:
                history = NotificationHistory(
                    user_id=user_id,
                    notification_type=type,
                    sent_at=datetime.utcnow()
                )
                db.add(history)

            # DO NOT call db.commit() here. The caller should manage the transaction.
            db.flush() 

            # Map type to icon/color if needed for frontend or just send payload
            payload = {
                "type": "new_notification",
                "payload": {
                    "id": notification.id,
                    "type": notification.type,
                    "title": notification.title,
                    "message": notification.message,
                    "link": notification.link,
                    "created_at": notification.created_at.isoformat(),
                    "is_read": notification.is_read
                }
            }

            # Safely attempt to send WebSocket notification
            try:
                import asyncio
                loop = None
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    pass
                
                if loop and loop.is_running():
                    loop.create_task(notification_manager.send_personal_message(json.dumps(payload), user_id))
                else:
                    logger.debug(f"Skipping WebSocket notification for user {user_id} - no running event loop")
            except Exception as inner_e:
                logger.warning(f"Could not send WebSocket notification: {inner_e}")

            return notification
        except Exception as e:
            logger.error(f"Error creating notification: {e}")
            db.rollback()
            return None
