# -*- coding: utf-8 -*-
from __future__ import print_function, division, absolute_import, unicode_literals
import re
import os
import sys
import signal
import random
import click
from oar.lib import (db, Job, JobType, AdmissionRule, Challenge, config)
from oar.kao.utils import get_date  # TODO move to oar.lib.utils

DEFAULT_VALUE = {
    'directory': os.environ['PWD'],
    'project': 'default',
    'signal': 12
    }

DEFAULT_CONFIG = {
    'OPENSSH_CMD': 'ssh',
    }
config.setdefault_config(DEFAULT_CONFIG)


def print_warning(*objs):
    print('# WARNING: ', *objs, file=sys.stderr)


def print_error(*objs):
    print('# ERROR: ', *objs, file=sys.stderr)


def qdel(signal, frame):
    '''To address ^C in interactive submission.'''
    # TODO launch a qdel
    print('TODO launch a qdel')

signal.signal(signal.SIGINT, qdel)
signal.signal(signal.SIGHUP, qdel)
signal.signal(signal.SIGPIPE, qdel)


def connect_job(job_id, stop_oarexec, openssh_cmd):
    '''Connect to a job and give the shell of the user on the remote host.'''
    # TODO connect_job
    print('TODO connect_job')


def resubmit_job(job_id):
    print('TODO resubmit_job')
    # TODO resubmit_job


def usage():
    '''Print usage message.'''
    ctx = click.get_current_context()
    click.echo(ctx.get_help())


# Move to oar.lib.utils or oar.lib.tools


def signal_almighty(remote_host, remote_port, msg):
    print('TODO signal_almighty')
    return 1

# Move to oar.lib.sub ??


def job_key_management():
    # TODO job_key_management
    pass


def add_micheline_subjob(job_type, resources, command, info_type, queue_name, properties,
                         reservation_time, file_id, checkpoint, signal, notify, name, env,
                         types, launching_directory, dependencies, stdout, stderr, job_hold,
                         project, ssh_private_key, ssh_public_key, initial_request, array_id, user,
                         reservation_field, start_time, array_index,
                         properties_applied_after_validation):
    # Test if properties and resources are coherent

    # Add admin properties to the job
    if properties_applied_after_validation:
        if properties:
            properties = '(' + properties + ') AND ' + properties_applied_after_validation
        else:
            properties = properties_applied_after_validation

    # TODO Verify the content of the ssh keys

    # TODO Check the user validity

    # TODO format job message
    message = ''
    # my $job_message = format_job_message_text($job_name,$estimated_nb_resources, $estimated_walltime, $jobType, $reservationField, $queue_name, $project, $type_list, '');

    # Insert job
    date = get_date()

    kwargs = {}
    kwargs['type'] = job_type
    kwargs['info_type'] = info_type
    kwargs['state'] = 'Hold'
    kwargs['job_user'] = user
    kwargs['command'] = command
    kwargs['submission_time'] = date
    kwargs['queue_name'] = queue_name
    kwargs['properties'] = properties
    kwargs['launching_directory'] = launching_directory
    kwargs['reservation'] = reservation_field
    kwargs['start_time'] = start_time
    kwargs['file_id'] = file_id
    kwargs['checkpoint'] = checkpoint
    kwargs['name'] = name
    kwargs['notify'] = notify
    kwargs['checkpoint_signal'] = signal
    kwargs['env'] = env
    kwargs['project'] = project
    kwargs['initial_request'] = initial_request
    kwargs['array_id'] = array_id
    kwargs['array_index'] = array_index
    kwargs['message'] = message

    ins = Job.__table__.insert().values(**kwargs)
    result = db.engine.execute(ins)
    job_id = result.inserted_primary_key[0]

    if array_id <= 0:
        db.query(Job).filter(Job.id == job_id).update({Job.array_id: job_id})
        db.commit()

    random_number = random.randint(1, 1000000000000)
    ins = Challenge.__table__.insert().values(
        {'job_id': job_id, 'challenge': random_number,
         'ssh_private_key': ssh_private_key, 'ssh_public_key': ssh_public_key})
    db.engine.execute(ins)

    if not stdout:
        stdout = 'OAR'
        if job_name:
            stdout += '.' + job_name
        stdout += '.%jobid%.stdout'
    else:
        stdout = re.sub(r'%jobname%', job_name, stdout)

    if not stderr:
        stderr = 'OAR'
        if job_name:
            stderr += '.' + job_name
        stderr += '.%jobid%.stderr'
    else:
        stderr = re.sub(r'%jobname%', job_name, stderr)

    # TODO resources request

    # types of job
    if types:
        ins = [{'job_id': job_id, 'type': typ} for typ in types]
        db.engine.execute(JobType.__table__.insert(), ins)

    # TODO dependencies with min_start_shift and max_start_shift
    if dependencies:
        ins = [{'job_id': job_id, 'job_id_required': dep} for dep in dependencies]
        db.engine.execute(JobDependencie.__table__.insert(), ins)
    #    foreach my $a (@{$anterior_ref}){
    #    if (my ($j,$min,$max) = $a =~ /^(\d+)(?:,([\[\]][-+]?\d+)?(?:,([\[\]][-+]?\d+)?)?)?$/) {
    #        $dbh->do("  INSERT INTO job_dependencies (job_id,job_id_required,min_start_shift,max_start_shift)
    #                    VALUES ($job_id,$j,'".(defined($min)?$min:"")."','".(defined($max)?$max:"")."')

    if not job_hold:
        req = db.insert(JobStateLog).values(
            {'job_id': jid, 'job_state': 'Waiting', 'date_start': date})
        db.engine.execute(req)
        db.commit

        db.query(Job).filter(Job.id == jid).update({Job.state: 'Waiting'})
        db.commit()
    else:
        req = db.insert(JobStateLog).values(
            {'job_id': jid, 'job_state': 'Hold', 'date_start': date})
        db.engine.execute(req)
        db.commit

    return(0, job_id)


def add_micheline_jobs():
    '''Adds a new job(or multiple in case of array-job) to the table Jobs applying
    the admission rules from the base  parameters : base, jobtype, nbnodes,
    , command, infotype, walltime, queuename, jobproperties,
    startTimeReservation
    return value : ref. of array of created jobids
    side effects : adds an entry to the table Jobs
                 the first jobid is found taking the maximal jobid from
                 jobs in the table plus 1, the next (if any) takes the next
                 jobid. Array-job submission is atomic and array_index are
                 sequential
                 the rules in the base are pieces of python code directly
                 evaluated here, so in theory any side effect is possible
                 in normal use, the unique effect of an admission rule should
                 be to change parameters
    '''

    array_id = 0
    start_time = '0'
    reservation_field = 'None'
    if reservation_time > 0:
        reservation_field = 'toSchedule'
        start_time = reservation_time

    user = os.environ['OARDO_USER']

    # TODO Verify notify syntax
    # if ((defined($notify)) and ($notify !~ m/^\s*(\[\s*(.+)\s*\]\s*)?(mail|exec)\s*:.+$/m)){
    #     warn("# Error: bad syntax for the notify option.\n");
    #    return(-6);
    # }

    # TODO Check the stdout and stderr path validity
    # if ((defined($stdout)) and ($stdout !~ m/^[a-zA-Z0-9_.\/\-\%\\ ]+$/m)) {
    #  warn("# Error: invalid stdout file name (bad character)\n.");
    #  return(-12);
    # }
    # if (defined($stderr) and ($stderr !~ m/^[a-zA-Z0-9_.\/\-\%\\ ]+$/m)) {
    #  warn("# Error: invalid stderr file name (bad character).\n");
    #  return(-13);
    # }

    # TODO Verify the content of env variables
    # if ( "$job_env" !~ m/^[\w\=\s\/\.\-\"]*$/m ){
    #   warn("# Error: the specified environnement variables contains bad characters -- $job_env\n");
    #    return(-9);
    # }

    # Retrieve Micheline's rules from the table
    rules = db.query(AdmissionRule.rule)\
              .filter(AdmissionRule.enabled == 'YES')\
              .order_by(AdmissionRule.priority, AdmissionRule.id)\
              .all()

    # This variable is used to add some resources properties restrictions but
    # after the validation (job is queued even if there are not enough
    # resources available)
    jobproperties_applied_after_validation = ''

    # Apply rules
    code = compile('\n'.join(rules), '<string>', 'exec')
    try:
        exec(code)
    except:
        err = sys.exc_info()
        print_error(err[1])
        print_error('a failed admission rule prevented submitting the job.')
        return -2

    # TODO Test if the queue exists
    # my %all_queues = get_all_queue_informations($dbh);
    # if (!defined($all_queues{$queue_name})){
    #    warn("# Error: queue $queue_name does not exist.\n");
    #    return(-8);
    # }
    if array_params:
        array_job_commands = [command + ' ' + params for params in array_job_commands]
    else:
        array_job_commands = [command] * array_job_nb

    array_index = 1
    job_id_list = []
    if array_job_nb > 1 and not use_job_key:
        # TODO Simple array job submissio
        # Simple array job submission is used
        pass
    else:
        # single job to submit or when job key is used with array job
        for cmd in array_job_commands:
            (error, ssh_private_key, ssh_public_key) = job_key_management(use_job_key,
                                                                        import_job_key_inline,
                                                                        import_job_key_file,
                                                                        export_job_key_file)
            if error != 0:
                print_error( 'job key generation and management failed :' + str(err))
                return(err, job_id_list)

            (error, job_id) = add_micheline_subjob()

            if error == 0:
                job_id_list.append(job_id)
            else:
                return(error, job_id_list)

            if array_id <= 0:
                array_id = job_id_list[0]
            array_index += 1

            if use_job_key and export_job_key_file:
                # TODO copy the keys in the directory specified with the right name
                pass

    return(0, job_id_list)


@click.command()
@click.argument('script', required=False)
@click.option('-I', '--interactive', is_flag=True,
              help='Interactive mode. Give you a shell on the first reserved node.')
@click.option('-q', '--queue', default='default',
              help='Specifies the destination queue. If not defined the job is enqueued in default queue')
@click.option('-l', '--resource', type=click.STRING, multiple=True,
              help="Defines resource list requested for a job: resource=value[,[resource=value]...]\
              Defined resources :\
              nodes : Request number of nodes (special value 'all' is replaced by the number of free\
              nodes corresponding to the weight and properties; special value ``max'' is replaced by\
              the number of free,absent and suspected nodes corresponding to the weight and properties).\
              walltime : Request maximun time. Format is [hour:mn:sec|hour:mn|mn]\
              weight : the weight that you want to reserve on each node")
@click.option('-p', '--property', type=click.STRING,
              help='Specify with SQL syntax reservation properties.')
@click.option('-r', '--reservation', type=click.STRING,
              help='Ask for an advance reservation job on the date in argument.')
@click.option('-C', '--connect', type=int,
              help='Connect to a reservation in Running state.')
@click.option('--array', type=int,
              help="Specify an array job with 'number' subjobs")
@click.option('--array-param-file', type=click.STRING,
              help='Specify an array job on which each subjob will receive one line of the \
              file as parameter')
@click.option('-S', '--scanscript', is_flag=False,
              help='Batch mode only: asks oarsub to scan the given script for OAR directives \
              (#OAR -l ...)')
@click.option('-k', '--checkpoint', type=int,
              help='Specify the number of seconds before the walltime when OAR will send \
              automatically a SIGUSR2 on the process.')
@click.option('--signal', type=int,
              help='Specify the signal to use when checkpointing Use signal numbers, \
              default is 12 (SIGUSR2)')
@click.option('-t', '--type', type=click.STRING, multiple=True,
              help='Specify a specific type (deploy, besteffort, cosystem, checkpoint, timesharing).')
@click.option('-d', '--directory', type=click.STRING,
              help='Specify the directory where to launch the command (default is current directory)')
@click.option('--project', type=click.STRING,
              help='Specify a name of a project the job belongs to.')
@click.option('--name', type=click.STRING,
              help='Specify an arbitrary name for the job')
@click.option('-a', '--after', type=click.STRING, multiple=True,
              help='Add a dependency to a given job, with optional min and max relative \
              start time constraints (<job id>[,m[,M]]).')
@click.option('--notify', type=click.STRING,
              help='Specify a notification method (mail or command to execute). Ex: \
              --notify "mail:name\@domain.com"\
              --notify "exec:/path/to/script args"')
@click.option('-k', '--use-job-key', is_flag=True,
              help='Activate the job-key mechanism.')
@click.option('-i', '--import-job-key-from-file', type=click.STRING,
              help='Import the job-key to use from a files instead of generating a new one.')
@click.option('--import-job-key-inline', type=click.STRING,
              help='Import the job-key to use inline instead of generating a new one.')
@click.option('-e', '--export-job-key-to-file', type=click.STRING,
              help='Export the job key to a file. Warning: the\
              file will be overwritten if it already exists.\
              (the %jobid% pattern is automatically replaced)')
@click.option('-O', '--stdout', type=click.STRING,
              help='Specify the file that will store the standart output \
              stream of the job. (the %jobid% pattern is automatically replaced)')
@click.option('-E', '--stderr', type=click.STRING,
              help='Specify the file that will store the standart error stream of the job.')
@click.option('--hold', is_flag=True,
              help='Set the job state into Hold instead of Waiting,\
              so that it is not scheduled (you must run "oarresume" to turn it into the Waiting state)')
@click.option('--resubmit', type=int,
              help='Resubmit the given job as a new one.')
@click.option('-s', '--stagein', type=click.Path(writable=False, readable=False),
              help='Set the stagein directory or archive.')
@click.option('-m', '--stagein-md5sum', type=int,
              help='Set the stagein file md5sum.')
@click.option('-V', '--version', is_flag=True,
              help='Print OAR version number.')
@click.option('--verbose', is_flag=True,
              help="Enables verbose output.")
def cli(script, interactive, queue, resource, reservation, connect, stagein, stagein_md5sum,
        type, checkpoint, verbose, property, resubmit, scanscript, project, signal,
        directory, name, after, notify, array, array_param_file,
        use_job_key, import_job_key_from_file, import_job_key_inline, export_job_key_to_file,
        stdout, stderr, hold, version):
    """Submit a job to OAR batch scheduler."""

    remote_host = config['SERVER_HOSTNAME']
    remote_port = config['SERVER_PORT']
    stageindir = config['STAGEIN_DIR']

    if 'OARSUB_DEFAULT_RESOURCES' in config:
        default_resources = config['OARSUB_DEFAULT_RESOURCES']
    else:
        default_resources = '/resource_id=1'

    # $Nodes_resources = get_conf("OARSUB_NODES_RESOURCES");
    # if (!defined($Nodes_resources)){
    #    $Nodes_resources = "resource_id";
    # }

    # $Deploy_hostname = get_conf("DEPLOY_HOSTNAME");
    # if (!defined($Deploy_hostname)){
    #    $Deploy_hostname = $remote_host;
    # }

    # $Cosystem_hostname = get_conf("COSYSTEM_HOSTNAME");
    # if (!defined($Cosystem_hostname)){
    #    $Cosystem_hostname = $remote_host;
    # }

    cpuset_field = config['JOB_RESOURCE_MANAGER_PROPERTY_DB_FIELD']
    cpuset_path = config['CPUSET_PATH']

    if 'OAR_RUNTIME_DIRECTORY' in config:
        pass
    # if (is_conf("OAR_RUNTIME_DIRECTORY")){
    #  OAR::Sub::set_default_oarexec_directory(get_conf("OAR_RUNTIME_DIRECTORY"));
    # }

    # my $default_oar_dir = OAR::Sub::get_default_oarexec_directory();
    # if (!(((-d $default_oar_dir) and (-O $default_oar_dir)) or (mkdir($default_oar_dir)))){
    #    die("# Error: failed to create the OAR directory $default_oar_dir, or bad permissions.\n");
    # }

    binpath = ''
    if 'OARDIR' in os.environ:
        binpath = os.environ['OARDIR'] + '/'
    else:
        print_error('OARDIR environment variable is not defined.')
        sys.exit(1)

    openssh_cmd = config['OPENSSH_CMD']
    ssh_timeout = config['OAR_SSH_CONNECTION_TIMEOUT']

    # if (is_conf("OAR_SSH_CONNECTION_TIMEOUT")){
    #    OAR::Sub::set_ssh_timeout(get_conf("OAR_SSH_CONNECTION_TIMEOUT"));
    # }

    # OAR version
    # TODO: OAR is now a set of composition...
    
    # Check the default name of the key if we have to generate it
    if ('OARSUB_FORCE_JOB_KEY' in config) and config['OARSUB_FORCE_JOB_KEY'] == 'yes':
        use_job_key = True
    else:
        use_job_key = False

    if resubmit:
        print('# Resubmitting job ', resubmit, '...')
        ret = resubmit_job(resubmit)
        if ret > 0:
            job_id = ret
            print(' done.\n')
            print('OAR_JOB_ID=' + str(job_id))
            if signal_almighty(remote_host, remote_port, 'Qsub') > 0:
                print_error('cannot connect to executor ' + str(remote_host) + ':' +
                            str(remote_port) + '. OAR server might be down.')
                sys.exit(3)
            else:
                sys.exit(0)
        else:
            print(' error.')
            if ret == -1:
                print_error('interactive jobs and advance reservations cannot be resubmitted.')
            elif ret == -2:
                print_error('only jobs in the Error or Terminated state can be resubmitted.')
            elif ret == -3:
                print_error('resubmitted job user mismatch.')
            elif ret == -4:
                print_error('another active job is using the same job key.')
            else:
                print_error('unknown error.')
            sys.exit(4)

    if not script and not interactive and not reservation and not connect:
        usage()
        sys.exit(5)

    if interactive and reservation:
        print_error('an advance reservation cannot be interactive.')
        usage()
        sys.exit(7)

    if interactive and any(re.match(r'^desktop_computing$', t) for t in type):
        print_error(' a desktop computing job cannot be interactive')
        usage()
        sys.exit(17)

    if any(re.match(r'^noop$', t) for t in type):
        if interactive:
            print_error('a NOOP job cannot be interactive.')
            sys.exit(17)
        elif connect:
            print_error('a NOOP job does not have a shell to connect to.')
            sys.exit(17)

    # TODO notify : check insecure character
    # if (defined($notify) && $notify =~ m/^.*exec\s*:.+$/m){
    # my $notify_exec_regexp = '[a-zA-Z0-9_.\/ -]+';
    # unless ($notify =~ m/.*exec\s*:($notify_exec_regexp)$/m){
    #  warn("# Error: insecure characters found in the notification method (the allowed regexp is: $notify_exec_regexp).\n");
    #  OAR::Sub::close_db_connection(); exit(16);
    # }
    # }

    # TODO   Connect to a reservation
    # Connect to a reservation
    # if (defined($connect_job)){
    # Do not kill the job if the user close the window
    #  $SIG{HUP} = 'DEFAULT';
    #  OAR::Sub::close_db_connection(); exit(connect_job($connect_job,0,$Openssh_cmd));
    # }

    if not project:
        project = DEFAULT_VALUE['project']
    if not checkpoint_signal:
        signal = DEFAULT_VALUE['signal']
    if not directory:
        directory = DEFAULT_VALUE['directory']

    if not interactive and script_cmd:
        if scanscript:
            # TODO scanscript
            pass

        # my @resource_list = parse_resource_descriptions(\@resource);

        array_job_nb = 1
        if array_param_file:
            pass
        # TODO
        # $array_params_ref = OAR::Sub::read_array_param_file($array_param_file);
        # $array_job_nb = scalar @{$array_params_ref};

        if array_job_nb == 0:
            print_error('an array job must have a number of sub-jobs greater than 0.')
            usage()
            sys.exit(6)

        cmd_executor = 'Qsub'
        if reservation:
            pass
        # TODO advance reservation

        # TODO: stagein machinery (dead feature ?) put in lib or remove ?
    
        (err, job_id_list) = add_micheline_jobs('PASSIVE', resources)
    else:
        # TODO interactive
        if script:
            print_warning('asking for an interactive job (-I), so ignoring arguments: ' + script + ' .')

        cmd_executor = 'Qsub -I'
        
