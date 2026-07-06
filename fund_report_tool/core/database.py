import os
from datetime import datetime
from pathlib import Path
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker
import yaml

from core.paths import CONFIG_PATH

Base = declarative_base()


class ProjectFinancial(Base):
    __tablename__ = 'project_financials'
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_name = Column(String(100), nullable=False, index=True)
    period = Column(String(20), nullable=False, index=True)
    report_type = Column(String(50))
    item_name = Column(String(100), nullable=False, index=True)
    value = Column(Float, default=0.0)
    source_file = Column(String(255))
    uploaded_at = Column(DateTime, default=datetime.now)


class FundFinancial(Base):
    __tablename__ = 'fund_financials'
    id = Column(Integer, primary_key=True, autoincrement=True)
    period = Column(String(20), nullable=False, index=True)
    report_type = Column(String(50))
    item_name = Column(String(100), nullable=False, index=True)
    value = Column(Float, default=0.0)
    source_file = Column(String(255))
    uploaded_at = Column(DateTime, default=datetime.now)


class ProjectMetric(Base):
    __tablename__ = 'project_metrics'
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_name = Column(String(100), nullable=False, index=True)
    period = Column(String(20), nullable=False, index=True)
    metric_name = Column(String(100), nullable=False)
    metric_value = Column(Float)
    calculated_at = Column(DateTime, default=datetime.now)


class FundMetric(Base):
    __tablename__ = 'fund_metrics'
    id = Column(Integer, primary_key=True, autoincrement=True)
    period = Column(String(20), nullable=False, index=True)
    metric_name = Column(String(100), nullable=False)
    metric_value = Column(Float)
    calculated_at = Column(DateTime, default=datetime.now)


class CleanLog(Base):
    __tablename__ = 'clean_logs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    file_path = Column(String(255))
    data_type = Column(String(20))
    status = Column(String(20))
    warnings = Column(Text)
    errors = Column(Text)
    created_at = Column(DateTime, default=datetime.now)


class FundFairValue(Base):
    __tablename__ = 'fund_fair_values'
    id = Column(Integer, primary_key=True, autoincrement=True)
    fund_name = Column(String(100), nullable=False, index=True)
    project_name = Column(String(100), nullable=False, index=True)
    period = Column(String(20), nullable=False, index=True)
    cost = Column(Float, default=0)
    fair_value = Column(Float, default=0)
    total_return = Column(Float, default=0)
    remark = Column(String(50))
    last_payment_date = Column(String(20))
    source_file = Column(String(255))
    uploaded_at = Column(DateTime, default=datetime.now)


def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def get_engine(db_path=None):
    if db_path is None:
        config = load_config()
        db_path = config['database']['path']
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else '.', exist_ok=True)
    return create_engine(f'sqlite:///{db_path}', echo=False)


def init_db(db_path=None):
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)


def get_session(db_path=None):
    engine = get_engine(db_path)
    Session = sessionmaker(bind=engine)
    return Session()
