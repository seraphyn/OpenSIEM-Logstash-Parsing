#!/usr/bin/env python3
import datetime
import logging
import os
import time
from logging.handlers import RotatingFileHandler
import requests
import kafka_producer
import secret

logger = logging.getLogger()
logger.setLevel('INFO')
log_path = 'log' + os.path.basename(__file__).split('.')[0] + '.log'

handler = RotatingFileHandler(
    log_path, maxBytes=1000000, backupCount=5)
formatter = logging.Formatter(
    "[%(asctime)s] {%(pathname)s:%(lineno)d} %(levelname)s - %(message)s")
handler.setLevel(logging.DEBUG)
handler.setFormatter(formatter)
logger.addHandler(handler)


def delete_files(directory):
    logs_directory = os.getcwd() + directory
    logger.info(f"deleting _read files in: {logs_directory}")
    filtered_files = [file for file in os.listdir(
        logs_directory) if file.endswith("read")]
    for file in filtered_files:
        path_to_file = os.path.join(logs_directory, file)
        logger.info(f"deleting {path_to_file}")
        os.remove(path_to_file)


def download_mcp_log(username, password, customer_id, directory, minutes_before):
    current_time = datetime.datetime.now()
    if minutes_before > 0:
        current_time = current_time - \
            datetime.timedelta(minutes=minutes_before)

    fifteen_minutes_ago = current_time - datetime.timedelta(minutes=15)
    CYCLE_TO = int(fifteen_minutes_ago.timestamp())

    twenty_minutes_ago = current_time - datetime.timedelta(minutes=20)
    CYCLE_FROM = int(twenty_minutes_ago.timestamp())
    FILTERS = f"filter.requestTimestampFrom={CYCLE_FROM}&filter.requestTimestampTo={CYCLE_TO}&order.0.requestTimestamp=asc"
    headers = {'Accept': 'text/csv', 'X-MWG-API-Version': '3'}
    url = f"https://msg.mcafeesaas.com/mwg/api/reporting/forensic/{customer_id}?{FILTERS}"

    try:
        name = os.getcwd() + directory + "mcp_" + str(CYCLE_TO) + ".csv"
        logger.info(f'requesting {directory}')
        with requests.get(url, auth=(username, password), headers=headers, stream=True) as r:
          r.raise_for_status()
          with open(name, 'wb') as local_file:
            logger.info(f'downloading {directory} started')
            for chunk in r.iter_content(chunk_size=8192):
              local_file.write(chunk)
            logger.info(f'downloading {directory} finished')
    except Exception as f:
        logger.error(f"The API call for {directory} failed")
        logger.error(f)
        return None


def run_mcp_download_job(minutes_before):
    logger.info('retrieving secrets for MCP')
    mcp_secrets = secret.get_secret('ngsiem-aca-logstash-api',
                                    ['mcp_username', 'mcp_password', 'mcp_customer_id', 'mcp_madjv_username', 'mcp_madjv_password',
                                     'mcp_madjv_customer_id', 'mcp_qa_username', 'mcp_qa_password', 'mcp_qa_customer_id'])
    try:
        # normal mcp
        download_mcp_log(
            mcp_secrets['mcp_username'], mcp_secrets['mcp_password'], mcp_secrets['mcp_customer_id'], '/mcp/', minutes_before)
    except Exception as e:
        logger.error(f"Error: {str(e)}")
    try:
        # madjv mcp
        download_mcp_log(mcp_secrets['mcp_madjv_username'], mcp_secrets['mcp_madjv_password'],
                         mcp_secrets['mcp_madjv_customer_id'], '/mcp_madjv/', minutes_before)
    except Exception as e:
        logger.error(f"Error: {str(e)}")
    try:
        # qa mcp
        download_mcp_log(mcp_secrets['mcp_qa_username'], mcp_secrets['mcp_qa_password'],
                         mcp_secrets['mcp_qa_customer_id'], '/mcp_qa/', minutes_before)
    except Exception as e:
        logger.error(f"Error: {str(e)}")


if __name__ == "__main__":
    '''
    This program will download mcp logs
    then read in files that start with *mcp*, post to kafka, and then the file it just read will be deleted
    log_security_mcafee.mcp_daily
    log_security_mcafee.mcp_qa_monthly
    log_security_mcafee.mcp_madjv_monthly
    '''
    minutes_before = 0*60
    minutes_before_file = os.path.join(os.getcwd(), 'minutes_before')
    if os.path.exists(minutes_before_file):
        with open(minutes_before_file, 'r') as minutes_file:
            line = minutes_file.readline()
            line = line.strip()
            minutes_before = int(line)
    while True:
        # download mcp file starting from minutes_before
        # send logs to kafka
        # reduce minutes_before in next iteration and repeat
        # when iteration reaches now -20 minutes
        # run the job once every 5 minutes
        logger.info(f'minutes before: {minutes_before}')
        if minutes_before <= 0:
            logger.info('waiting for 5 minutes')
            time.sleep(300)

        logger.info('mcp_download started')
        run_mcp_download_job(minutes_before)
        logger.info('mcp_download finished')
        minutes_before = minutes_before - 5

        logger.info('mcp_produce started')
        kafka_producer.set_creds()
        # MCP daily
        kafka_producer.produce_csv_to_kafka("log_security_mcafee.mcp_daily", '/mcp/')
        delete_files('/mcp/')
        # MCP MADJV
        kafka_producer.produce_csv_to_kafka(
            "log_security_mcafee.mcp_madjv_monthly", '/mcp_madjv/')
        delete_files('/mcp_madjv/')
        # MCP QA
        kafka_producer.produce_csv_to_kafka("log_security_mcafee.mcp_qa_monthly", '/mcp_qa/')
        delete_files('/mcp_qa/')
        logger.info('mcp_produce finished')
        with open(minutes_before_file, 'w') as minutes_file:
            minutes_before = 0 if minutes_before < 0 else minutes_before
            minutes_file.write(str(minutes_before))
