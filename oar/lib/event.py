# coding: utf-8

from sqlalchemy import func
from oar.lib import (db, EventLog, EventLogHostname, get_logger)

import oar.lib.tools as tools

logger = get_logger('oar.lib.event')


def add_new_event(ev_type, job_id, description):
    """Add a new entry in event_log table""" 
    event_data = EventLog(type=ev_type, job_id=job_id, date=tools.get_date(),
                          description=description[:255])
    db.add(event_data)


def add_new_event_with_host(ev_type, job_id, description, hostnames):
    
    ins = EventLog.__table__.insert().values(
        {'type': ev_type, 'job_id': job_id, 'date': tools.get_date(),
         'description': description[:255]})
    result = db.session.execute(ins)
    event_id = result.inserted_primary_key[0]
    
    #Forces unique values in hostnames by using set and
    #fills the EventLogHostname
    for hostname in set(hostnames):
        db.add(EventLogHostname(event_id=event_id, hostname=hostname))


def is_an_event_exists(job_id, event):
    res = db.query(func.count(EventLog.id)).filter(EventLog.job_id == job_id)\
                                           .filter(EventLog.type == event)\
                                           .scalar()
    return res


def get_job_events(job_id):
    """Get events for the specified job"""
    result = db.query(EventLog).filter(EventLog.job_id == job_id).all()
    return result

def get_to_check_events():
    """"Get all events with toCheck field on YES"""
    result = db.query(EventLog).filter(EventLog.to_check == 'YES').order_by(EventLog.id).all()
    return result

def check_event(event_type, job_id):
    """Turn the field toCheck into NO"""
    db.query(EventLog).filter(EventLog.job_id == job_id)\
                      .filter(EventLog.type == event_type)\
                      .filter(EventLog.to_check == 'YES').update({'to_check': 'NO'})
    db.commit()

def get_hostname_event(event_id):
    """Get hostnames corresponding to an event Id"""
    return db.query(EventLogHostname.hostname).filter(EventLogHostname.event_id == event_id).all()
