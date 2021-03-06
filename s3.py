import collections
import os
import re
import sys

_EXIT_STATUS_MISSING_DEPENDENCY = 2

try:
    import boto.s3.connection
    import boto.s3.key
except ImportError:
    sys.stderr.write("Error: missing the Python boto module.\n")
    sys.exit(_EXIT_STATUS_MISSING_DEPENDENCY)

import api
import util
import youtube


s3_access_key = open(os.path.expanduser('~/s3_access_key')).read().strip()
s3_secret_key = open(os.path.expanduser('~/s3_secret_key')).read().strip()

# We use bucket names with uppercase characters, so we must use
# OrdinaryCallingFormat instead of the default SubdomainCallingFormat
s3_connection = boto.s3.connection.S3Connection(
    s3_access_key, s3_secret_key, 
    calling_format=boto.s3.connection.OrdinaryCallingFormat())

converted_bucket = s3_connection.get_bucket("KA-youtube-converted")
unconverted_bucket = s3_connection.get_bucket("KA-youtube-unconverted")

# Keys (inside buckets) are in the format YOUTUBE_ID.FORMAT
# e.g. DK1lCc9b7bg.m3u8/ or Dpo_-GrMpNE.mp4-low/
re_video_key_name = re.compile(r"([\w-]+)\.([\w-]+)/")

# Older keys are of the form YOUTUBE_ID
re_legacy_video_key_name = re.compile(r"([\w-]+)/(.*)$")


def get_or_create_unconverted_source_url(youtube_id):
    matching_keys = list(unconverted_bucket.list(youtube_id))

    # TODO(alpert): How do these .part files get created? They're not real
    # video files and should be ignored.
    matching_keys = [key for key in matching_keys
                     if not key.name.endswith('.part')]
                     
    matching_key = None

    if matching_keys:
        if len(matching_keys) > 1:
            util.logger.warning("More than 1 matching unconverted video "
                                "URL found for video {0}".format(youtube_id))
        matching_key = matching_keys[0]
    else:
        util.logger.info("Unconverted video not available on s3 yet, "
                         "downloading from youtube to create it.")

        video_path = youtube.download(youtube_id)
        if not video_path:
            message = "Error downloading video {0}".format(youtube_id)
            util.logger.warning(message)
            return
        util.logger.info("Downloaded video to {0}".format(video_path))

        video_extension = os.path.splitext(video_path)[1]
        assert video_extension[0] == "."
        video_extension = video_extension[1:]
        if video_extension not in ["flv", "mp4"]:
            message = ("Unrecognized video extension {0} when downloading "
                       "video {1} from YouTube".format(video_extension,
                                                       youtube_id))
            util.logger.warning(message)

        matching_key = boto.s3.key.Key(unconverted_bucket,
                                       "{0}/{0}.{1}".format(youtube_id,
                                                            video_extension))
        matching_key.set_contents_from_filename(video_path)

        os.remove(video_path)
        util.logger.info("Deleted {0}".format(video_path))

    return "s3://{0}/{1}".format(unconverted_bucket.name, matching_key.name)


def list_converted_formats():
    """Get map of youtube_ids (keys) to set of available converted formats."""
    converted_videos = collections.defaultdict(set)
    legacy_video_keys = set()
    for key in converted_bucket.list(delimiter="/"):
        video_match = re_video_key_name.match(key.name)
        if video_match is None:
            if re_legacy_video_key_name.match(key.name) is not None:
                legacy_video_keys.add(key.name)
            else:
                util.logger.warning("Unrecognized key {0} is not in format "
                                    "YOUTUBE_ID.FORMAT/".format(key.name))
        else:
            converted_videos[video_match.group(1)].add(video_match.group(2))
    util.logger.info("{0} legacy converted videos were ignored".format(
        len(legacy_video_keys)))
    return converted_videos


def list_legacy_mp4_videos():
    """Return a set of legacy converted youtube_ids.
    
    Returns youtube ids of videos that have legacy mp4/png converted
    content saved on S3. You can pass these ids to
    copy_legacy_content_to_new_location.
    """
    legacy_mp4_videos = set()
    for key in converted_bucket.list(delimiter="/"):
        legacy_match = re_legacy_video_key_name.match(key.name)
        if legacy_match is not None:
            legacy_mp4_videos.add(legacy_match.group(1))
    return legacy_mp4_videos


def copy_legacy_content_to_new_location(youtube_id):
    """Copy MP4 & PNG files from a legacy-format video to new naming scheme.
    """
    for key in converted_bucket.list(prefix="{0}/".format(youtube_id)):
        legacy_match = re_legacy_video_key_name.match(key.name)
        assert legacy_match is not None
        assert legacy_match.group(1) == youtube_id
        dest_key = "{0}.mp4/{1}".format(youtube_id, legacy_match.group(2))
        util.logger.info("Copying {0} to {1}".format(key.name, dest_key))
        key.copy(converted_bucket.name, dest_key, preserve_acl=True)


def list_missing_converted_formats():
    """Return a map of youtube_ids to a set of missing formats on S3."""
    missing_converted_formats = {}
    converted_formats = list_converted_formats()
    for youtube_id in api.get_youtube_ids():
        missing_converted_formats[youtube_id] = (
            util.DOWNLOADABLE_FORMATS - converted_formats[youtube_id])
    return missing_converted_formats
