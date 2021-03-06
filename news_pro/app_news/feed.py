#! /usr/bin/python
# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
from pytz import timezone
import json
import requests
import re
import urllib2

import logging
import feedparser
from twython import Twython, TwythonRateLimitError
from BeautifulSoup import BeautifulSoup
from requests import ConnectionError

from django.conf import settings
from django.core.exceptions import MultipleObjectsReturned
from models import ArticleModel, StatisticArticle, InternetTime, URLS
from urllib3.contrib import pyopenssl
pyopenssl.inject_into_urllib3()

logger = logging.getLogger('error')


def daterange(start_date, end_date):
    """
    Return all dates in range from start_date to end_date
    """
    for n in range(int((end_date - start_date).days)+1):
        yield start_date + timedelta(n)


def get_shares_fb_total(full_url):
    """
    Get fb shares for specific url
    """
    result = 0
    try:
        fb_request = requests.get(
            "https://graph.facebook.com/?id={}&access_token={}"
            .format(unicode(full_url).encode('utf-8'), settings.FACEBOOK_ACCESS_TOKEN)
        )
    except ConnectionError as e:
        # TODO add logging
        print(e)
    else:
        if fb_request.status_code == 200:
            fb = fb_request.json().get('share')
            if fb:
                result = fb.get('share_count', 0)
    finally:
        return result


def get_shares_vk_total(full_url):
    """
    Get vk shares for specific url
    """
    re_mask = '^VK.Share.count\([\d+], (\d+)\);$'
    rq_text = requests.get(
        "http://vk.com/share.php?act=count&url={}".format(unicode(full_url).encode('utf-8'))
        ).text
    match = re.match(re_mask, rq_text)
    return int(match.groups()[0]) if match else 0


def get_shares_twitter(full_url):
    """
    Get twitter shares for specific url
    """
    twitter = Twython(settings.TWITTER_APP_KEY,
                      settings.TWITTER_APP_SECRET,
                      settings.TWITTER_OAUTH_TOKEN,
                      settings.TWITTER_OAUTH_TOKEN_SECRET)
    search = twitter.search(q=unicode(full_url).encode('utf-8'))['statuses']
    return len(search)


def get_attendances(article):
    """
    Get attendances for specific url
    """
    try:
        moscow_time = datetime.now(timezone('Europe/Moscow')).date()
        all_visits = 0
        for day in daterange(article.datetime.date(), moscow_time):
            page = urllib2.urlopen(
                'http://www.liveinternet.ru/stat/ukrpravda/pages.html?type=only&filter=%s&date=%s-%s-%s&lang=en&ok=+OK+&report=pages.html' %
                (article.link[11:], day.year, day.month, day.day)).read()
            soup = BeautifulSoup(page)
            soup.prettify()
            total = soup.find(text="total")
            td_tag = total.parent
            today_visit = td_tag.findNext('td')
            all_visits += int(today_visit.contents[0].replace(',', ''))
        return all_visits
    except (urllib2.HTTPError, urllib2.URLError, AttributeError) as err:
        logger.error(err)
    return 0


def get_article_from_pravda(rss_link, source):
    rssfeed = feedparser.parse(rss_link)
    internet_time = InternetTime.get_internet_time()
    for each in rssfeed.entries:
        if 'pravda.com.ua' in each['link']:
            try:
                article, cr = ArticleModel.objects.get_or_create(link=each['link'])
            except MultipleObjectsReturned:
                all_articles = ArticleModel.objects.filter(link=each['link'])
                article = all_articles[0]
                cr = False
                map(lambda x: x.delete(), all_articles[1:])
            if cr:
                naive_date_str = each['published'].rpartition(' ')[0]
                naive_dt = datetime.strptime(naive_date_str,
                                             '%a, %d %b %Y %H:%M:%S')
                article.title = each['title']
                article.datetime = naive_dt
                article.source = source
                article.internet_time = internet_time
                article.save()


def get_pravda_articles():
    """
    Get rss feed from pravda.com.ua and get new articles from it
    """
    for rss_link in URLS['pravda']:
        get_article_from_pravda(rss_link, 1)
    for rss_link in URLS['pravda_news']:
        get_article_from_pravda(rss_link, 4)


def get_site_ua_articles():
    """
    Get rss feed from site.ua and get new articles from it
    """
    for rss_link in URLS['site_ua']:
        rssfeed = feedparser.parse(rss_link)
        internet_time = InternetTime.get_internet_time()
        for each in rssfeed.entries:
            try:
                article, cr = ArticleModel.objects.get_or_create(link=each['link'])
            except MultipleObjectsReturned:
                all_articles = ArticleModel.objects.filter(link=each['link'])
                article = all_articles[0]
                cr = False
                map(lambda x: x.delete(), all_articles[1:])
            if cr:
                naive_date_str = each['published'].rpartition(' ')[0]
                naive_dt = datetime.strptime(naive_date_str,
                                             '%a, %d %b %Y %H:%M:%S')
                article.title = each['title']
                article.datetime = naive_dt
                article.source = 2
                article.internet_time = internet_time
                article.save()


def get_nyt_articles():
    """
    Parse NYT site and new articles from it
    """
    page = urllib2.urlopen(URLS['nyt']).read()
    soup = BeautifulSoup(page)
    today = datetime.now()
    soup.prettify()
    internet_time = InternetTime.get_internet_time()
    searcn_div = soup.find('div', {"id": "searchList"})
    if searcn_div:
        for each in searcn_div.findAll('h4'):
            link = each.find('a')['href']
            title = each.find('a').text
            time = datetime.strptime(each.findNext('h6').text, '%B %d, %Y, %A')
            time = time.replace(hour=today.hour, minute=today.minute)
            (article, cr) = ArticleModel.objects.get_or_create(link=link)
            if cr:
                article.title = title
                article.datetime = time
                article.source = 3
                article.internet_time = internet_time
                article.save()


def check_articles_shares():
    """
    Get all shares data for articles that were published less
    then 48 hours from now
    """
    now_minus_48 = datetime.today() - timedelta(hours=48)
    internet_time = InternetTime.get_internet_time()
    active_articles = ArticleModel.objects.filter(datetime__gte=now_minus_48).\
        order_by('datetime')
    for each in active_articles:
        try:
            shares_twitter = get_shares_twitter(each.link)
        except TwythonRateLimitError:
            shares_twitter = 0
        shares_fb = get_shares_fb_total(each.link)
        try:
            shares_vk = get_shares_vk_total(each.link)
        except ConnectionError:
            shares_vk = 0
        attendance = get_attendances(each) if each.source in [1, 4] else None
        stat = StatisticArticle(
                        article=each,
                        shares_fb=shares_fb,
                        shares_twitter=shares_twitter,
                        internet_time=internet_time-float(each.internet_time),
                        shares_vk=shares_vk,
                        attendance=attendance
                                )
        stat.save()
