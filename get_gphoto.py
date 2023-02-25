#!/usr/bin/env python3
# coding: utf8

from pathlib import Path
import subprocess
import requests
import logging
import os
from random import randrange

from gphotos.authorize import Authorize
from gphotos.restclient import RestClient

log = logging.getLogger()
logging.basicConfig(filename='gphotos_python.log',
                    filemode='w+', # Collect logs
                    level=os.getenv('LOG_LEVEL', logging.INFO))

DEVICE_TYPE = 'PW2'
IMAGE_CROP_TYPE = 'smart'
IMAGE_SELECTION_STRATEGY = 'latest'
GPHOTOS_ALBUM_NAME = 'kindle'

# ###############################################################
OUTPUT_FILENAME = 'photo.jpg'
SEEN_IMAGES_LIST = os.path.join('data','images_seen.txt')
MAX_PAGE_SIZE = 50
PAGE_SIZE = MAX_PAGE_SIZE

# Autorotation and processing require imagemagick
IMAGEMAGICK_PATH = '/mnt/us/linkss/bin/convert'
KINDLE_COLORS_GIF_PATH = '/mnt/us/linkss/etc/kindle_colors.gif'

IMAGE_CROP_TYPES = {
    'regular': 'c',
    'smart': 'p'
}
IMAGE_SIZES = {
    '2048': {
        'long': 2048,
        'short': 1024
    },
    'PW2': {
        'long': 1024,
        'short': 758
    },
    'PW3': {
        'long': 1448,
        'short': 1072
    }
}

def _get_image_size_string(is_vertical=False):
    if is_vertical:
        width = IMAGE_SIZES[DEVICE_TYPE]['short']
        height = IMAGE_SIZES[DEVICE_TYPE]['long']
    else:
        width = IMAGE_SIZES[DEVICE_TYPE]['long']
        height = IMAGE_SIZES[DEVICE_TYPE]['short']
    return f'=w{width}-h{height}-{IMAGE_CROP_TYPES[IMAGE_CROP_TYPE]}'


class ImageSelectionStrategies():
    # FIXME: this should not be a class; methods should be in own
    # file instead

    # photo_list looks like this (by default, ordered by creation_date, newest last):
    # {'mediaItems': [
    #     {
    #         'id': '<long string>',
    #         'productUrl': 'https://photos.google.com/lr/album/<long string>/photo/<long string>',
    #         'baseUrl': 'https://lh3.googleusercontent.com/lr/<very very long string>',
    #         'mimeType': 'image/jpeg',
    #         'mediaMetadata': {
    #             'creationTime': '2023-01-17T12:40:36Z',
    #             'width': '4032',
    #             'height': '2268',
    #             'photo': {
    #                 'cameraMake': 'samsung',
    #                 'cameraModel': 'SM-G973F',
    #                 'focalLength': 4.32,
    #                 'apertureFNumber': 2.4,
    #                 'isoEquivalent': 80,
    #                 'exposureTime': '0.008333333s'
    #             }
    #         },
    #         'filename': '20230117_134036.heic'
    #     },
    #     ...
    # ]}

    @staticmethod
    def random(photo_list):
        log.info("Selecting image at random")
        items_count = len(photo_list['mediaItems'])
        media_item = None
        while(not media_item):
            idx = randrange(items_count)
            log.debug(idx)
            if "image/jpeg" in photo_list['mediaItems'][idx]['mimeType']:
                media_item = photo_list['mediaItems'][idx]
        return media_item

    @staticmethod
    def latest(photo_list):
        """Get the 'latest' image
        
        Keeps track of fetched images and selects the most
        recent, unseen one because the list only contains
        the creation date, not the date when a picture was
        added to the album.
        """
        log.info("Selecting latest image")
        try:
            with open(SEEN_IMAGES_LIST, 'r') as reader:
                seen_images = reader.readlines()
        except FileNotFoundError as ex:
            log.warning(f"{SEEN_IMAGES_LIST} does not exist!")
            seen_images = []

        image_key_template = "{filename} {creation_time}"

        image = None
        for item in reversed(photo_list['mediaItems']):
            image = item
            image_key = image_key_template.format(filename=item['filename'],
                                                  creation_time=item['mediaMetadata']['creationTime'])
            if image_key in seen_images or f"{image_key}\n" in seen_images:
                log.info(f"We have already seen '{image_key}'")
                continue
            log.info(f"We didn't see this last time: '{image_key}'")
            break
        
        # Save the list of images we know about
        with open(SEEN_IMAGES_LIST, 'w+') as writer:
            image_keys = [
                f"{image_key_template.format(filename=i['filename'], creation_time=i['mediaMetadata']['creationTime'])}\n"
                for i in photo_list['mediaItems']
            ]
            writer.writelines(image_keys)

        return image


### Main app class
class KindleGphotos:
    def __init__(self):
        self.auth: Authorize = None

    def setup(self):
        credentials_file = Path(".gphotos.token")
        secret_file = Path("client_secret.json")
        scope = [
            "https://www.googleapis.com/auth/photoslibrary.readonly",
            "https://www.googleapis.com/auth/photoslibrary.sharing",
        ]
        photos_api_url = (
            "https://photoslibrary.googleapis.com/$discovery" "/rest?version=v1"
        )

        self.auth = Authorize(scope, credentials_file, secret_file, 3)

        log.info("Authorizing...")
        self.auth.authorize()
        self.google_photos_client = RestClient(photos_api_url, self.auth.session)

    def start(self):
        log.info("Starting up...")
        ### Get album list
        # FIXME: get all pages, not just first one!
        mylist = self.google_photos_client.sharedAlbums.list.execute(pageSize=PAGE_SIZE).json()

        ### Get album
        album = _pick_album(mylist, GPHOTOS_ALBUM_NAME)

        if not int(album['mediaItemsCount']):
            log.error("Album is empty!")
            raise Exception

        ### Get list of images
        body = {
                "pageSize": PAGE_SIZE,
                "albumId":  album['id'],
            #    "filters": {
            #        "mediaTypeFilter": {"mediaTypes":  ["PHOTO"]},
            #    },
            }
        log.info(f"Fetching photos from '{album['title']}'")
        # FIXME: fetch all pages, not just the first one!
        photo_list = self.google_photos_client.mediaItems.search.execute(body).json()
        strategies = ImageSelectionStrategies()
        media_item = getattr(strategies, IMAGE_SELECTION_STRATEGY)(photo_list)

        ### Download photo
        log.info(f"Fetching {media_item['filename']}")
        orientation = 'vertical' if media_item['mediaMetadata']['height'] > media_item['mediaMetadata']['width'] else 'horizontal'
        is_vertical = orientation == 'vertical'
        log.info(f"Is vertical?: {is_vertical}")
        url = str(media_item['baseUrl']) + _get_image_size_string(is_vertical)
        photo = requests.get(url)
        open(OUTPUT_FILENAME, 'wb').write(photo.content)
        print(f"Downloaded {media_item['filename']}")
        _post_process_photo(OUTPUT_FILENAME, is_vertical)

    def main(self):
        self.setup()
        self.start()


def _pick_album(album_list, title):
    log.info(f"Searching for album '{title}'")
    for album in album_list['sharedAlbums']:
        if 'title' in album.keys():
            log.debug(f"Found album '{album['title']}' with {album['mediaItemsCount']} items")
            if album['title'] == title:
                log.info("Found the right album!")
                return album
    log.warning(f"No album titled '{title}' found!")
    return None


def _post_process_photo(file, is_vertical):
    """Post process a photo
    Requires the screensaver hack to be installed
    """
    log.info(f"Attempting to post-process '{file}'")
    command = (
        f"{IMAGEMAGICK_PATH} {file}"
        #" -auto-orient "
        #" -resize x758 "
        #" -gravity center"
        f" -rotate {270 if not is_vertical else 0}"
        " -filter LanczosSharp"
        " -brightness-contrast 3x15"
        " -gravity center"
        " +repage "
        " -colorspace Gray "
        " -dither FloydSteinberg "
        f" -remap {KINDLE_COLORS_GIF_PATH} "
        " -quality 75"
        " -define png:color-type=0"
        " -define png:bit-depth=8"
        f" {file}.png"
    )
    try:
        proc = subprocess.run(command, shell=True)
        proc.check_returncode()
        log.info("Post-processing complete")
        print("Post-processed the file")
    except Exception as ex:
        log.error("Unable to post-process the file")


if __name__ == '__main__':
    KindleGphotos().main()
    exit(0)
