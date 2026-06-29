from sqlalchemy import String, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class UserConfig(Base):
    __tablename__ = "user_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    theme: Mapped[str] = mapped_column(String(20), default="dark")
    always_on_top: Mapped[bool] = mapped_column(Boolean, default=True)
    font_size: Mapped[int] = mapped_column(Integer, default=14)
    show_floating_icon: Mapped[bool] = mapped_column(Boolean, default=True)
    floating_icon_x: Mapped[int] = mapped_column(Integer, default=-1)
    floating_icon_y: Mapped[int] = mapped_column(Integer, default=-1)
    # off / low / medium / high
    proactive_chat_level: Mapped[str] = mapped_column(String(10), default="medium")
    proactive_silent_tool_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    proactive_auto_see_screen: Mapped[bool] = mapped_column(Boolean, default=True)
    proactive_daily_limit: Mapped[int] = mapped_column(Integer, default=10)
    proactive_fixed_schedule_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    proactive_system_notification: Mapped[bool] = mapped_column(Boolean, default=True)
    proactive_daily_count: Mapped[int] = mapped_column(Integer, default=0)
    proactive_state_date: Mapped[str] = mapped_column(String(20), default="")
    proactive_scheduled_slots: Mapped[str] = mapped_column(String(100), default="")
    language: Mapped[str] = mapped_column(String(10), default="zh")
    last_daily_greeting_at: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_daily_greeting_date: Mapped[str] = mapped_column(String(20), default="")
    location_city: Mapped[str] = mapped_column(String(100), default="")
    location_country: Mapped[str] = mapped_column(String(100), default="")
