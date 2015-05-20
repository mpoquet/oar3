import ipdb
import time
import os
import re
import subprocess

from oar.lib import (config, db, Queue, get_logger, GanttJobsPredictionsVisu,
                     GanttJobsResourcesVisu)

from oar.kao.job import (get_current_not_waiting_jobs,
                         get_gantt_jobs_to_launch,
                         add_resource_job_pairs, set_job_state,
                         get_gantt_waiting_interactive_prediction_date,
                         frag_job, get_waiting_reservation_jobs_specific_queue,
                         set_job_resa_state, set_job_message,
                         get_waiting_reservations_already_scheduled,
                         USE_PLACEHOLDER, NO_PLACEHOLDER, JobPseudo,
                         save_assigns, set_job_start_time_assigned_moldable_id,
                         get_jobs_in_multiple_states, gantt_flush_tables,
                         get_scheduled_no_AR_jobs, get_waiting_scheduled_AR_jobs,
                         remove_gantt_resource_job, set_moldable_job_max_time,
                         set_gantt_job_startTime, get_jobs_on_resuming_job_resources,
                         resume_job_action)

from oar.kao.utils import (local_to_sql, add_new_event, duration_to_sql, get_date,
                           get_job_events, send_checkpoint_signal)

import oar.kao.utils as utils

import oar.kao.tools as tools

from oar.kao.platform import Platform
from oar.kao.slot import (SlotSet, Slot, intersec_ts_ph_itvs_slots, intersec_itvs_slots,
                          MAX_TIME)
from oar.kao.scheduling import (set_slots_with_prev_scheduled_jobs, get_encompassing_slots,
                                find_resource_hierarchies_job)

from oar.kao.interval import (intersec, equal_itvs,sub_intervals, itvs2ids, itvs_size)

from oar.kao.node import (search_idle_nodes, get_gantt_hostname_to_wake_up,
                          get_next_job_date_on_node, get_last_wake_up_date_of_node)

from oar.kao.hulot import (halt_nodes, wake_up_nodes, check_signal)

# Constant duration time of a besteffort job *)
besteffort_duration = 300  # TODO conf ???

# TODO : not used, to confirm
# timeout for validating reservation
#reservation_validation_timeout = 30

# Set undefined config value to default one
default_config = {
    "DB_PORT": "5432",
    "HIERARCHY_LABEL": "resource_id,network_address",
    "SCHEDULER_RESOURCE_ORDER": "resource_id ASC",
    "SCHEDULER_JOB_SECURITY_TIME": 60,
    "SCHEDULER_AVAILABLE_SUSPENDED_RESOURCE_TYPE": "default",
    "FAIRSHARING_ENABLED": "no",
    "SCHEDULER_FAIRSHARING_MAX_JOB_PER_USER": 30,
    "RESERVATION_WAITING_RESOURCES_TIMEOUT": 300,
    "SCHEDULER_TIMEOUT": 10,
    "ENERGY_SAVING_INTERNAL": "no",
    "SCHEDULER_NODE_MANAGER_WAKEUP_TIME": 1
}

config.setdefault_config(default_config)

# for key, value in config.iteritems():
#        print key, value

# waiting time when a reservation has not all of its nodes
reservation_waiting_timeout = int(config['RESERVATION_WAITING_RESOURCES_TIMEOUT'])

config['LOG_FILE'] = '/dev/stdout'
# Log category
log = get_logger("oar.kao.meta_sched")

exit_code = 0

# stock the job ids that where already send to almighty
to_launch_jobs_already_treated = {}

# order_part = config["SCHEDULER_RESOURCE_ORDER"]

##########################################################################
# Initialize Gantt tables with scheduled reservation jobs, Running jobs,
# toLaunch jobs and Launching jobs;
##########################################################################


def plt_init_with_running_jobs(initial_time_sec, job_security_time):

    plt = Platform()
    #
    # Determine Global Resource Intervals and Initial Slot
    #
    resource_set = plt.resource_set()
    initial_slot_set = SlotSet((resource_set.roid_itvs, initial_time_sec))

    log.debug("Processing of processing of already handled reservations")
    accepted_ar_jids, accepted_ar_jobs = \
                                         get_waiting_reservations_already_scheduled(resource_set, job_security_time)
    gantt_flush_tables(accepted_ar_jids)

    log.debug("Processing of current jobs")
    current_jobs = get_jobs_in_multiple_states(['Running', 'toLaunch', 'Launching', 
                                                'Finishing', 'Suspended', 'Resuming'],
                                               resource_set)
    
    save_assigns(current_jobs, resource_set)

    #
    #  Resource availabilty (Available_upto field) is integrated through pseudo job
    #
    pseudo_jobs = []
    for t_avail_upto in sorted(resource_set.available_upto.keys()):
        itvs = resource_set.available_upto[t_avail_upto]
        j = JobPseudo()
        # print t_avail_upto, max_time - t_avail_upto, itvs
        j.start_time = t_avail_upto
        j.walltime = MAX_TIME- t_avail_upto
        j.res_set = itvs
        j.ts = False
        j.ph = NO_PLACEHOLDER

        pseudo_jobs.append(j)

    if pseudo_jobs != []:
        initial_slot_set.split_slots_jobs(pseudo_jobs)

    #
    # Get already scheduled jobs advanced reservations and jobs from more higher priority queues
    #
    # TODO?: Remove resources of the type specified in
    # SCHEDULER_AVAILABLE_SUSPENDED_RESOURCE_TYPE
    scheduled_jobs = plt.get_scheduled_jobs(
        resource_set, job_security_time, initial_time_sec)


    # retrieve ressources used by besteffort jobs
    besteffort_rid2job = {}
    for job in scheduled_jobs:
        if 'besteffort' in job.types:
             for r_id in itvs2ids(job.res_set):
                 besteffort_rid2job[r_id] = job

    # Create and fill gantt
    all_slot_sets = {'default': initial_slot_set}
    if scheduled_jobs != []:
        filter_besteffort = True
        set_slots_with_prev_scheduled_jobs(all_slot_sets, scheduled_jobs,
                                           job_security_time, filter_besteffort)

    return (plt, all_slot_sets, resource_set, besteffort_rid2job)


# Tell Almighty to run a job
def notify_to_run_job(jid):

    if jid not in to_launch_jobs_already_treated:
        if 0:  # TODO OAR::IO::is_job_desktop_computing
            log.debug(str(jid) + ": Desktop computing job, I don't handle it!")
        else:
            nb_sent = utils.notify_almighty("OARRUNJOB_" + str(jid) + '\n')
            if nb_sent:
                to_launch_jobs_already_treated[jid] = 1
                log.debug("Notify almighty to launch the job" + str(jid))
            else:
                log.warn(
                    "Not able to notify almighty to launch the job " + str(jid) + " (socket error)")

# Prepare a job to be run by bipbip


def prepare_job_to_be_launched(job, current_time_sec):

    # TODO ???
    # my $running_date = $current_time_sec;
    # if ($running_date < $job_submission_time){
    #    $running_date = $job_submission_time;
    # }

    # OAR::IO::set_running_date_arbitrary($base, $job_id, $running_date);
    # OAR::IO::set_assigned_moldable_job($base, $job_id, $moldable_job_id);

    # set start_time an for jobs to launch
    set_job_start_time_assigned_moldable_id(job.id, 
                                            current_time_sec, 
                                            job.moldable_id)

    # fix resource assignement
    add_resource_job_pairs(job.moldable_id)

    set_job_state(job.id, "toLaunch")

    notify_to_run_job(job.id)



def handle_waiting_reservation_jobs(queue_name, resource_set, job_security_time, current_time_sec):

    log.debug("Queue " + queue_name +
              ": begin processing of accepted advance reservations")

    ar_jobs = get_waiting_scheduled_AR_jobs(queue_name, resource_set, job_security_time, current_time_sec)
                                
    for job in ar_jobs:

        moldable_id = job.moldable_id
        walltime = job.walltime

        # Test if AR job is expired and handle it
        if (current_time_sec > (job.start_time + walltime)):
            log.warn("[" + str(job.id) + 
                     "] set job state to Error: avdance reservation expired and couldn't be started")
            set_job_state(job.id, "Error")
            set_job_message(job.id, "Reservation expired and couldn't be started.")
        else:

            # Determine current available ressources
            avail_res = intersec(resource_set.roid_itvs, job.res_set)
        
            # Test if the AR job is waiting to be launched due to nodes' unavailabilities 
            if (avail_res == []) and (job.start_time < current_time_sec):
                log.warn("[" + str(job.id) + 
                     "] advance reservation is waiting because no resource is present")

                # Delay launching time
                set_gantt_job_startTime(moldable_id, current_time_sec + 1);
            elif (job.start_time < current_time_sec):
                if (job.start_time + reservation_waiting_timeout) > current_time_sec:
                    if not equal_itvs(avail_res, job.res_set):
                        # The expected ressources are not all available, 
                        # wait the specified timeout
                        log.warn("[" + str(job.id) +
                                 "] advance reservation is waiting because not all \
                                  resources are available yet")
                        set_gantt_job_startTime(moldable_id, current_time_sec + 1)
                else:
                    #It's time to launch the AR job, remove missing ressources
                    missing_resources_itvs = sub_intervals(job.res_set, avail_res)
                    remove_gantt_resource_job(moldable_id, missing_resources_itvs,
                                              resource_set)
                    log.warn("[" + str(job.id) + 
                             "remove some resources assigned to this advance reservation, \
                              because there are not Alive")
                            
                    add_new_event("SCHEDULER_REDUCE_NB_RESSOURCES_FOR_ADVANCE_RESERVATION", 
                                  job.id, 
                                  "[MetaSched] Reduce the number of resources for the job "
                                  + str(job.id))

                    nb_res = itvs_size(job.res_set) - itvs_size(missing_resources_itvs)
                    new_message = re.sub(r'R=\d+','R=' + str(nb_res), job.message)
                    if new_message != job.message:
                        set_job_message(job.id,new_message)

    log.debug("Queue " + queue_name +
              ": end processing of reservations with missing resources")


def check_reservation_jobs(plt, resource_set, queue_name, all_slot_sets, current_time_sec):
    '''Processing of new reservations'''

    log.debug("Queue " + queue_name + ": begin processing of new reservations")

    ar_jobs_scheduled = {}

    ar_jobs, ar_jids, nb_ar_jobs = plt.get_waiting_jobs(
        queue_name, 'toSchedule')
    log.debug("nb_ar_jobs:" + str(nb_ar_jobs))

    if nb_ar_jobs > 0:
        job_security_time = config["SCHEDULER_JOB_SECURITY_TIME"]
        plt.get_data_jobs(ar_jobs, ar_jids, resource_set, job_security_time)

        log.debug("Try and schedule new reservations")
        for jid in ar_jids:
            job = ar_jobs[jid]
            log.debug(
                "Find resource for Advance Reservation job:" + str(job.id))

            # It is a reservation, we take care only of the first moldable job
            moldable_id, walltime, hy_res_rqts = job.mld_res_rqts[0]

            # test if reservation is too old
            if current_time_sec >= (job.start_time + walltime):
                log.warn(
                    "[" + str(job.id) + "] Canceling job: reservation is too old")
                set_job_message(job.id, "Reservation too old")
                set_job_state(job.id, "toError")
                continue
            else:
                if job.start_time < current_time_sec:
                    # TODO update to DB ????
                    job.start_time = current_time_sec

            ss_name = 'default'

            # TODO container
            # if "inner" in job.types:
            #    ss_name = job.types["inner"]
            # TODO: test if container is an AR job

            slots = all_slot_sets[ss_name].slots

            t_e = job.start_time + walltime - job_security_time
            sid_left, sid_right = get_encompassing_slots(
                slots, job.start_time, t_e)

            if job.ts or (job.ph == USE_PLACEHOLDER):
                itvs_avail = intersec_ts_ph_itvs_slots(
                    slots, sid_left, sid_right, job)
            else:
                itvs_avail = intersec_itvs_slots(slots, sid_left, sid_right)

            itvs = find_resource_hierarchies_job(
                itvs_avail, hy_res_rqts, resource_set.hierarchy)

            if itvs == []:
                # not enough resource available
                log.warn("[" + str(job.id) +
                         "] advance reservation cannot be validated, not enough resources")
                set_job_state(job.id, "toError")
                set_job_message(job.id, "This advance reservation cannot run")
            else:
                # The reservation can be scheduled
                log.debug(
                    "[" + str(job.id) + "] advance reservation is validated")
                job.moldable_id = moldable_id
                job.res_set = itvs
                ar_jobs_scheduled[job.id] = job
                # if "container" in job.types:
                #    slot = Slot(1, 0, 0, job.res_set[:], job.start_time,
                #                job.start_time + job.walltime - job_security_time)
                # slot.show()
                #    slots_sets[job.id] = SlotSet(slot)

                set_job_state(job.id, "toAckReservation")

            set_job_resa_state(job.id, "Scheduled")

    if ar_jobs_scheduled != []:
        log.debug("Save AR jobs' assignements in database")
        save_assigns(ar_jobs_scheduled, resource_set)

    log.debug("Queue " + queue_name + ": end processing of new reservations")


def check_besteffort_jobs_to_kill(jobs_to_launch, rid2jid_to_launch,current_time_sec, 
                                  besteffort_rid2job, resource_set):
    
    '''Detect if there are besteffort jobs to kill
    return 1 if there is at least 1 job to frag otherwise 0
    '''

    return_code = 0

    log.debug("Begin processing of besteffort jobs to kill")
    
    fragged_jobs = []

    for rid, job_id in rid2jid_to_launch.iteritems():
        if rid in besteffort_rid2job:
            be_job = besteffort_rid2job[rid]
            job_toLaunch = jobs_to_launch[job_id] 

            if is_timesharing_for_2_jobs(be_job, job_toLaunch):
                log.debug("Resource " + str(rid) +
                          " is needed for  job " + str(job_id) +
                          ", but besteffort job  " + str(be_job.id) +
                          " can live, because timesharing compatible")
            else:
                if be_job.id not in fragged_jobs:
                    skip_kill = 0;
                    checkpoint_first_date = sys.maxsize
                    # Check if we must checkpoint the besteffort job
                    if be_job.checkpoint > 0:
                        for ev in get_job_events(be_job.id):
                            if ev.type == 'CHECKPOINT':
                                if checkpoint_first_date > ev.date:
                                     checkpoint_first_date = ev.date
                    if (checkpoint_first_date == sys.maxsize) or\
                       (current_time_sec <= (checkpoint_first_date + be_job.checkpoint)):
                        skip_kill = 1
                        send_checkpoint_signal(be_job)

                        log.debug("Send checkpoint signal to the job " + str(be_job.id))
                        
                    if skip_kill == 0:

                        log.debug("Resource " + str(rid) + 
                                  "need to be freed for job " + str(be_job.id) + 
                                  ": killing besteffort job " + str(job_toLaunch.id))

                        add_new_event("BESTEFFORT_KILL",be_job.id,
                                      "kill the besteffort job " + str(be_job.id))
                        frag_job(be_job_id)
                        
                    fragged_jobs[be_job.id] = 1
                    return_code = 1


    log.debug("End precessing of besteffort jobs to kill\n")

    return return_code


def handle_jobs_to_launch(jobs_to_launch, current_time_sec, current_time_sql):
    log.debug(
        "Begin processing of jobs to launch (start time <= " + current_time_sql)

    return_code = 0
    
    for job in jobs_to_launch:
        return_code = 1
        log.debug("Set job " + str(job.id) + " state to toLaunch at " + current_time_sql)

        #
        # Advance Reservation
        #
        if ((job.reservation == "Scheduled") and (job.start_time < current_time_sec)):
            max_time = walltime - (current_time_sec - job.start_time)
         
            set_moldable_job_max_time(job.moldable_id, max_time)
            set_gantt_job_startTime(job.moldable_id, current_time_sec)
            log.warn("Reduce walltime of job " + str(job.id) +
                     "to " + str(max_time) + "(was  " + str(walltime) + " )")

            add_new_event("REDUCE_RESERVATION_WALLTIME", job.id, 
                          "Change walltime from " + str(walltime) + " to "
                          + str(max_time))

            w_max_time = duration_to_sql(max_time)
            new_message = re.sub(r'W=\d+:\d+:\d+','W=' + w_max_time, job.message)

            if new_message != job.message:
                set_job_message(job.id,new_message)

        prepare_job_to_be_launched(job, current_time_sec)

    log.debug("End processing of jobs to launch")

    return return_code


def update_gantt_visualization():

    db.query(GanttJobsPredictionsVisu).delete()
    db.query(GanttJobsResourcesVisu).delete()
    db.commit()

    sql_queries = ["INSERT INTO gantt_jobs_predictions_visu SELECT * FROM gantt_jobs_predictions",
                   "INSERT INTO gantt_jobs_resources_visu SELECT * FROM gantt_jobs_resources"
                   ]
    for query in sql_queries:
        db.engine.execute(query)


def call_extern_scheduler():
    pass

def call_intern_scheduler():
    pass


def meta_schedule(mode='extern'):

    exit_code = 0

    job_security_time = config["SCHEDULER_JOB_SECURITY_TIME"]
        
    utils.init_judas_notify_user()
    utils.create_almighty_socket()


    log.debug(
        "Retrieve information for already scheduled reservations from database before flush (keep assign resources)")

    # reservation ??.

    initial_time_sec = get_date() #time.time()
    initial_time_sql = local_to_sql(initial_time_sec)

    current_time_sec = initial_time_sec
    current_time_sql = initial_time_sql

    plt, all_slot_sets, resource_set, besteffort_rid2jid = plt_init_with_running_jobs(
        initial_time_sec, job_security_time)

    if "OARDIR" in os.environ:
        binpath = os.environ["OARDIR"] + "/"
    else:
        binpath = "/usr/local/lib/oar/"
        log.warning(
            "OARDIR env variable must be defined, " + binpath + " is used by default")

    for queue in db.query(Queue).order_by("priority DESC").all():

        if queue.state == "Active":
            log.debug("Queue " + queue.name + ": Launching scheduler " + queue.scheduler_policy + " at time "
                      + initial_time_sql)

            cmd_scheduler = binpath + "schedulers/" + queue.scheduler_policy

            child_launched = True
            # TODO TO CONFIRM
            sched_exit_code = 0
            sched_signal_num = 0
            sched_dumped_core = 0
            try:
                child = subprocess.Popen([cmd_scheduler, queue.name, str(
                    initial_time_sec), initial_time_sql], stdout=subprocess.PIPE)

                for line in iter(child.stdout.readline, ''):
                    log.debug("Read on the scheduler output:" + line.rstrip())
                # TODO SCHEDULER_LAUNCHER_OPTIMIZATION
                # if
                # ((get_conf_with_default_param("SCHEDULER_LAUNCHER_OPTIMIZATION",
                # "yes") eq "yes") and

                child.wait()
                rc = child.returncode
                sched_exit_code, sched_signal_num, sched_dumped_core = rc >> 8, rc & 0x7f, bool(
                    rc & 0x80)

            except OSError as e:
                child_launched = False
                log.warn(str(e) + " Cannot run: " + cmd_scheduler + " " + queue.name + " " +
                         str(initial_time_sec) + " " + initial_time_sql)

            if (not child_launched) or (sched_signal_num != 0) or (sched_dumped_core != 0):
                log.error("Execution of " + queue.scheduler_policy +
                          " failed, inactivating queue " + queue.name + " (see `oarnotify')")
                # stop queue
                db.query(Queue).filter_by(name=queue.name).update(
                    {"state": "notActive"})

            if sched_exit_code != 0:
                log.error("Scheduler " + queue.scheduler_policy + " returned a bad value: " + str(sched_exit_code) +
                          ". Inactivating queue " + queue.scheduler_policy + " (see `oarnotify')")
                # stop queue
                db.query(Queue).filter_by(name=queue.name).update(
                    {"state": "notActive"})

            
            #retrieve job and assignement decision from previous scheduling step
            scheduled_jobs = get_scheduled_no_AR_jobs(queue.name, resource_set, 
                                                      job_security_time, initial_time_sec)


            if scheduled_jobs != []:
                if queue == "besteffort":
                    filter_besteffort = False
                else:
                    filter_besteffort = True
                    set_slots_with_prev_scheduled_jobs(all_slot_sets,
                                                       scheduled_jobs,
                                                       job_security_time,
                                                       filter_besteffort)


            handle_waiting_reservation_jobs(queue.name, resource_set, 
                                            job_security_time, current_time_sec)

            #handle_new_AR_jobs
            check_reservation_jobs(
                plt, resource_set, queue.name, all_slot_sets, current_time_sec)



    jobs_toL, rid2jid_toL = get_gantt_jobs_to_launch(resource_set, 
                                                     job_security_time, 
                                                     current_time_sec)

    if check_besteffort_jobs_to_kill(jobs_toL, rid2jid_toL,
                                     current_time_sec, besteffort_rid2jid, 
                                     resource_set) == 1:
        # We must kill some besteffort jobs
        utils.notify_almighty("ChState")
        exit_code = 2
    elif handle_jobs_to_launch(jobs_toL, current_time_sec, current_time_sql) == 1:
        exit_code = 0

    # Update visu gantt tables
    update_gantt_visualization()

    # Manage dynamic node feature
    flagHulot = False
    timeout_cmd = config['SCHEDULER_TIMEOUT']

    if ((("SCHEDULER_NODE_MANAGER_SLEEP_CMD" in config) or  
        ((config["ENERGY_SAVING_INTERNAL"] == "yes") and 
         ("ENERGY_SAVING_NODE_MANAGER_SLEEP_CMD" in config))) and
        (("SCHEDULER_NODE_MANAGER_SLEEP_TIME" in config) 
         and ("SCHEDULER_NODE_MANAGER_IDLE_TIME" in config))):


        # Look at nodes that are unused for a duration
        idle_duration = config["SCHEDULER_NODE_MANAGER_IDLE_TIME"]
        sleep_duration = config["SCHEDULER_NODE_MANAGER_SLEEP_TIME"]
    
        idle_nodes = search_idle_nodes(current_time_sec)
        tmp_time = current_time_sec - idle_duration

        node_halt = []

        for node, idle_duration in idle_nodes.iteritems():
            if idle_duration < tmp_time:
                # Search if the node has enough time to sleep
                tmp = get_next_job_date_on_node(node);
                if  (tmp is None) or (tmp - sleep_duration > current_time_sec):
                    # Search if node has not been woken up recently
                    wakeup_date = get_last_wake_up_date_of_node(node)
                    if (wakeup_date is None) or (wakeup_date < tmp_time):
                        node_halt.append(node)


        if node_halt != []:
            log.debug("Powering off some nodes (energy saving): " + str(node_halt))
            # Using the built-in energy saving module to shut down nodes
            if config["ENERGY_SAVING_INTERNAL"] == "yes":
                if halt_nodes(node_halt):
                        log.error("Communication problem with the energy saving module (Hulot)\n")
            
                flagHulot = 1
            else:
                # Not using the built-in energy saving module to shut down nodes
                cmd = config["SCHEDULER_NODE_MANAGER_SLEEP_CMD"]
                if tools.fork_and_feed_stdin(cmd, timeout_cmd, node_halt):
                    log.error("Command " + cmd + "timeouted (" + str(timeout_cmd) 
                              + "s) while trying to  poweroff some nodes") 

    
    if (("SCHEDULER_NODE_MANAGER_SLEEP_CMD" in config) or 
        ((config["ENERGY_SAVING_INTERNAL"] == "yes") and 
         ("ENERGY_SAVING_NODE_MANAGER_SLEEP_CMD" in config))):
        # Get nodes which the scheduler wants to schedule jobs to, 
        # but which are in the Absent state, to wake them up
        wakeup_time = config["SCHEDULER_NODE_MANAGER_WAKEUP_TIME"]
        nodes = get_gantt_hostname_to_wake_up(current_time_sec, wakeup_time)

        if nodes != []:
            log.debug("Awaking some nodes: " + str(nodes))        
            # Using the built-in energy saving module to wake up nodes
            if config["ENERGY_SAVING_INTERNAL"] == "yes":
                if wake_up_nodes(nodes):
                    log.error("Communication problem with the energy saving module (Hulot)")
                flagHulot = 1
            else:
                # Not using the built-in energy saving module to wake up nodes
                cmd = config["SCHEDULER_NODE_MANAGER_WAKE_UP_CMD"]
                if tools.fork_and_feed_stdin(cmd, timeout_cmd, nodes):
                    log.error("Command " + cmd + "timeouted (" + str(timeout_cmd) 
                              + "s) while trying to wake-up some nodes ") 
                    

    # Send CHECK signal to Hulot if needed                
    if not flagHulot and (config["ENERGY_SAVING_INTERNAL"] == "yes"):
        if check_signal():
            log.error("Communication problem with the energy saving module (Hulot)")

        
    # Retrieve jobs according to their state and excluding job in 'Waiting' state.        
    jobs_by_state = get_current_not_waiting_jobs()

    # Search jobs to resume
    if "Resuming" in jobs_by_state:
        for job in jobs_by_state["Resuming"]:
            other_jobs = get_jobs_on_resuming_job_resources(job.id)
            # TODO : look for timesharing other jobs. What do we do?????
            if other_jobs == []:
                # We can resume the job
                log.debug("[" + str(job.id) +"] Resuming job")
                if 'noop' in job.types:
                    resume_job_action(job.id)
                    log.debug("[" + str(job_id) + "] Resume NOOP job OK")
                else:
                    tools.resume_job(job)


    # Notify oarsub -I when they will be launched
    for j_info in get_gantt_waiting_interactive_prediction_date():
        job_id, job_info_type, job_start_time, job_message = j_info
        addr, port = job_info_type.split(':')
        new_start_prediction = local_to_sql(job_start_time)
        log.debug("[" + str(job_id) + "] Notifying user of the start prediction: " +
                  new_start_prediction + "(" + job_message + ")")
        utils.notify_tcp_socket(addr, port, "[" + initial_time_sql + "] Start prediction: " +
                          new_start_prediction + " (" + job_message + ")")

    # Run the decisions
    # Process "toError" jobs
    if "toError" in jobs_by_state:
        for job in jobs_by_state["toError"]:
            addr, port = job.info_type.split(':')
            if job.type == "INTERACTIVE" or\
               (job.type == "PASSIVE" and job.reservation == "Scheduled"):
                log.debug("Notify oarsub job (num:" + str(job.id) + ") in error; jobInfo=" +
                          job.info_type)

                nb_sent1 = utils.notify_tcp_socket(addr, port, job.message + '\n')
                nb_sent2 = utils.notify_tcp_socket(addr, port, "BAD JOB" + '\n')
                if (nb_sent1 == 0) or (nb_sent2 == 0):
                    log.warn(
                        "Cannot open connection to oarsub client for" + str(job.id))
            log.debug("Set job " + str(job.id) + " to state Error")
            set_job_state(job.id, "Error")

    # Process toAckReservation jobs
    if "toAckReservation" in jobs_by_state:
        for job in jobs_by_state["toAckReservation"]:
            addr, port = job.info_type.split(':')
            log.debug(
                " Treate job" + str(job.id) + " in toAckReservation state")

            nb_sent = utils.notify_tcp_socket(addr, port, "GOOD RESERVATION" + '\n')

            if nb_sent == 0:
                log.warn(
                    "Frag job " + str(job.id) + ", I cannot notify oarsub for the reservation")
                add_new_event("CANNOT_NOTIFY_OARSUB", str(
                    job.id), "Can not notify oarsub for the job " + str(job.id))

                # TODO ???
                # OAR::IO::lock_table / OAR::IO::unlock_table($base)
                frag_job(job.id)

                exit_code = 2
            else:
                log.debug("Notify oarsub for a RESERVATION (idJob=" +
                          str(job.id) + ") --> OK; jobInfo=" + job.info_type)
                set_job_state(job.id, "Waiting")
                if ((job.start_time - 1) <= current_time_sec) and (exit_code == 0):
                    exit_code = 1

    # Process toLaunch jobs
    if "toLaunch" in jobs_by_state:
        for job in jobs_by_state["toLaunch"]:
            notify_to_run_job(job.id)

    log.debug("End of Meta Scheduler")

    return exit_code
