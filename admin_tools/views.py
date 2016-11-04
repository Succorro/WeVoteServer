# admin_tools/views.py
# Brought to you by We Vote. Be good.
# -*- coding: UTF-8 -*-

from candidate.controllers import candidates_import_from_sample_file
from config.base import get_environment_variable, LOGIN_URL
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.messages import get_messages
from django.core.urlresolvers import reverse
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.shortcuts import render
from election.models import Election
from election.controllers import elections_import_from_sample_file
from email_outbound.models import EmailAddress
from follow.models import FollowOrganizationList
from friend.models import CurrentFriend, FriendManager
from import_export_facebook.models import FacebookLinkToVoter, FacebookManager
from import_export_google_civic.models import GoogleCivicApiCounterManager
from import_export_vote_smart.models import VoteSmartApiCounterManager
from office.controllers import offices_import_from_sample_file
from organization.controllers import organizations_import_from_sample_file
from organization.models import Organization, OrganizationManager
from polling_location.controllers import import_and_save_all_polling_locations_data
from position.controllers import fetch_positions_count_for_this_voter, \
    find_organizations_referenced_in_positions_for_this_voter, positions_import_from_sample_file
from position.models import PositionEntered, PositionForFriends
from twitter.models import TwitterLinkToOrganization, TwitterLinkToVoter, TwitterUserManager
from voter.models import Voter, VoterAddressManager, VoterDeviceLinkManager, VoterManager, voter_has_authority, \
    voter_setup
from wevote_functions.functions import convert_to_int, delete_voter_api_device_id_cookie, generate_voter_device_id, \
    get_voter_api_device_id, positive_value_exists, set_voter_api_device_id, STATE_CODE_MAP

BALLOT_ITEMS_SYNC_URL = get_environment_variable("BALLOT_ITEMS_SYNC_URL")
BALLOT_RETURNED_SYNC_URL = get_environment_variable("BALLOT_RETURNED_SYNC_URL")
ELECTIONS_SYNC_URL = get_environment_variable("ELECTIONS_SYNC_URL")
ORGANIZATIONS_SYNC_URL = get_environment_variable("ORGANIZATIONS_SYNC_URL")
OFFICES_SYNC_URL = get_environment_variable("OFFICES_SYNC_URL")
CANDIDATES_SYNC_URL = get_environment_variable("CANDIDATES_SYNC_URL")
MEASURES_SYNC_URL = get_environment_variable("MEASURES_SYNC_URL")
POLLING_LOCATIONS_SYNC_URL = get_environment_variable("POLLING_LOCATIONS_SYNC_URL")
POSITIONS_SYNC_URL = get_environment_variable("POSITIONS_SYNC_URL")
VOTER_GUIDES_SYNC_URL = get_environment_variable("VOTER_GUIDES_SYNC_URL")


@login_required
def admin_home_view(request):
    authority_required = {'verified_volunteer'}  # admin, verified_volunteer
    if not voter_has_authority(request, authority_required):
        return redirect_to_sign_in_page(request, authority_required)

    # Create a voter_device_id and voter in the database if one doesn't exist yet
    results = voter_setup(request)
    voter_api_device_id = results['voter_api_device_id']
    store_new_voter_api_device_id_in_cookie = results['store_new_voter_api_device_id_in_cookie']

    google_civic_election_id = convert_to_int(request.GET.get('google_civic_election_id', 0))

    template_values = {
        'google_civic_election_id': google_civic_election_id,
    }
    response = render(request, 'admin_tools/index.html', template_values)

    # We want to store the voter_api_device_id cookie if it is new
    if positive_value_exists(voter_api_device_id) and positive_value_exists(store_new_voter_api_device_id_in_cookie):
        set_voter_api_device_id(request, response, voter_api_device_id)

    return response


@login_required
def data_cleanup_view(request):
    authority_required = {'admin'}  # admin, verified_volunteer
    if not voter_has_authority(request, authority_required):
        return redirect_to_sign_in_page(request, authority_required)

    template_values = {
    }
    response = render(request, 'admin_tools/data_cleanup.html', template_values)

    return response


@login_required
def data_cleanup_organization_analysis_view(request):
    """
    Analyze a single organization
    :param request:
    :return:
    """
    authority_required = {'admin'}  # admin, verified_volunteer
    if not voter_has_authority(request, authority_required):
        return redirect_to_sign_in_page(request, authority_required)

    organization_we_vote_id = request.GET.get('organization_we_vote_id')
    organization_found = False
    organization = Organization()

    twitter_link_to_this_organization_exists = False
    twitter_link_to_another_organization_exists = False
    try:
        organization = Organization.objects.get(we_vote_id__iexact=organization_we_vote_id)
        organization_found = True
        try:
            organization.linked_voter = Voter.objects.get(
                linked_organization_we_vote_id__iexact=organization.we_vote_id)
        except Voter.DoesNotExist:
            pass

        try:
            twitter_link_to_organization = TwitterLinkToOrganization.objects.get(
                organization_we_vote_id__iexact=organization.we_vote_id)
            if positive_value_exists(twitter_link_to_organization.twitter_id):
                twitter_link_to_this_organization_exists = True
                organization.twitter_id_from_link_to_organization = twitter_link_to_organization.twitter_id
                # We reach out for the twitter_screen_name
                organization.twitter_screen_name_from_link_to_organization = \
                    twitter_link_to_organization.fetch_twitter_handle_locally_or_remotely()
        except TwitterLinkToOrganization.DoesNotExist:
            pass
    except Organization.MultipleObjectsReturned as e:
        pass
    except Organization.DoesNotExist:
        pass

    # If this organization doesn't have a TwitterLinkToOrganization for the local twitter data,
    #  check to see if anyone else owns it.
    if not twitter_link_to_this_organization_exists and organization.twitter_user_id:
        try:
            twitter_link_to_organization = TwitterLinkToOrganization.objects.get(
                twitter_id=organization.twitter_user_id)
            if positive_value_exists(twitter_link_to_organization.twitter_id):
                if twitter_link_to_organization.organization_we_vote_id != organization.we_vote_id:
                    twitter_link_to_another_organization_exists = True
        except TwitterLinkToOrganization.DoesNotExist:
            pass

    # Voter that is linked to this Organization
    voter_linked_organization_we_vote_id_list = Voter.objects.all()
    voter_linked_organization_we_vote_id_list = voter_linked_organization_we_vote_id_list.filter(
        linked_organization_we_vote_id__iexact=organization.we_vote_id)
    voter_linked_organization_we_vote_id_list = voter_linked_organization_we_vote_id_list[:10]

    voter_linked_organization_we_vote_id_list_updated = []
    for one_linked_voter in voter_linked_organization_we_vote_id_list:
        if positive_value_exists(one_linked_voter.we_vote_id):
            try:
                twitter_link_to_voter = TwitterLinkToVoter.objects.get(
                    voter_we_vote_id__iexact=one_linked_voter.we_vote_id)
                if positive_value_exists(twitter_link_to_voter.twitter_id):
                    one_linked_voter.twitter_id_from_link_to_voter = twitter_link_to_voter.twitter_id
                    # We reach out for the twitter_screen_name
                    one_linked_voter.twitter_screen_name_from_link_to_voter = \
                        twitter_link_to_voter.fetch_twitter_handle_locally_or_remotely()
            except TwitterLinkToVoter.DoesNotExist:
                pass

        voter_linked_organization_we_vote_id_list_updated.append(one_linked_voter)

    organization_list_with_duplicate_twitter_updated = []
    if organization_found:
        organization_filters = []
        if positive_value_exists(organization.twitter_user_id):
            new_organization_filter = Q(twitter_user_id=organization.twitter_user_id)
            organization_filters.append(new_organization_filter)
        if positive_value_exists(organization.organization_twitter_handle):
            new_organization_filter = Q(organization_twitter_handle__iexact=organization.organization_twitter_handle)
            organization_filters.append(new_organization_filter)

        if len(organization_filters):
            final_organization_filters = organization_filters.pop()

            # ...and "OR" the remaining items in the list
            for item in organization_filters:
                final_organization_filters |= item

            organization_list_with_duplicate_twitter = Organization.objects.all()
            organization_list_with_duplicate_twitter = organization_list_with_duplicate_twitter.filter(
                final_organization_filters)
            organization_list_with_duplicate_twitter = organization_list_with_duplicate_twitter.exclude(
                we_vote_id__iexact=organization_we_vote_id)

            for one_duplicate_organization in organization_list_with_duplicate_twitter:
                try:
                    linked_voter = Voter.objects.get(linked_organization_we_vote_id__iexact=one_duplicate_organization.we_vote_id)
                    one_duplicate_organization.linked_voter = linked_voter
                except Voter.DoesNotExist:
                    pass

                organization_list_with_duplicate_twitter_updated.append(one_duplicate_organization)

    # Voters that share the same local twitter data
    # (excluding voter linked to this org with linked_organization_we_vote_id)
    voter_raw_filters = []
    if positive_value_exists(organization.twitter_user_id):
        new_voter_filter = Q(twitter_id=organization.twitter_user_id)
        voter_raw_filters.append(new_voter_filter)
    if positive_value_exists(organization.organization_twitter_handle):
        new_voter_filter = Q(twitter_screen_name__iexact=organization.organization_twitter_handle)
        voter_raw_filters.append(new_voter_filter)

    voter_list_duplicate_twitter_updated = []
    if len(voter_raw_filters):
        final_voter_filters = voter_raw_filters.pop()

        # ...and "OR" the remaining items in the list
        for item in voter_raw_filters:
            final_voter_filters |= item

        voter_list_duplicate_twitter = Voter.objects.all()
        voter_list_duplicate_twitter = voter_list_duplicate_twitter.filter(final_voter_filters)
        voter_list_duplicate_twitter = voter_list_duplicate_twitter.exclude(
            linked_organization_we_vote_id__iexact=organization.we_vote_id)
        voter_list_duplicate_twitter = voter_list_duplicate_twitter

        for one_duplicate_voter in voter_list_duplicate_twitter:
            try:
                twitter_link_to_voter = TwitterLinkToVoter.objects.get(
                    voter_we_vote_id__iexact=one_duplicate_voter.we_vote_id)
                if positive_value_exists(twitter_link_to_voter.twitter_id):
                    one_duplicate_voter.twitter_id_from_link_to_voter = twitter_link_to_voter.twitter_id
                    # We reach out for the twitter_screen_name
                    one_duplicate_voter.twitter_screen_name_from_link_to_voter = \
                        twitter_link_to_voter.fetch_twitter_handle_locally_or_remotely()
            except TwitterLinkToVoter.DoesNotExist:
                pass

                voter_list_duplicate_twitter_updated.append(one_duplicate_voter)

    messages_on_stage = get_messages(request)

    template_values = {
        'messages_on_stage':                        messages_on_stage,
        'organization':                             organization,
        'voter_linked_organization_we_vote_id_list': voter_linked_organization_we_vote_id_list_updated,
        'organization_list_with_duplicate_twitter': organization_list_with_duplicate_twitter_updated,
        'voter_list_duplicate_twitter':             voter_list_duplicate_twitter_updated,
        'twitter_link_to_this_organization_exists':     twitter_link_to_this_organization_exists,
        'twitter_link_to_another_organization_exists':  twitter_link_to_another_organization_exists,
    }
    response = render(request, 'admin_tools/data_cleanup_organization_analysis.html', template_values)

    return response


@login_required
def data_cleanup_organization_list_analysis_view(request):
    """
    Analyze all organizations
    :param request:
    :return:
    """
    authority_required = {'admin'}  # admin, verified_volunteer
    if not voter_has_authority(request, authority_required):
        return redirect_to_sign_in_page(request, authority_required)

    create_twitter_link_to_organization = request.GET.get('create_twitter_link_to_organization', False)

    organization_list = Organization.objects.all()

    # Goal: Create TwitterLinkToOrganization
    # Goal: Look for opportunities to link Voters with Orgs

    # Cycle through all organizations and identify which ones have duplicate Twitter data so we know
    #  we need to take a deeper look
    organizations_with_twitter_id = {}
    organizations_with_twitter_handle = {}
    duplicate_twitter_id_count = 0
    duplicate_twitter_handle_count = 0
    for one_organization in organization_list:
        if positive_value_exists(one_organization.twitter_user_id):
            twitter_user_id_string = str(one_organization.twitter_user_id)
            if twitter_user_id_string in organizations_with_twitter_id:
                organizations_with_twitter_id[twitter_user_id_string] += 1
            else:
                organizations_with_twitter_id[twitter_user_id_string] = 1
            if organizations_with_twitter_id[twitter_user_id_string] == 2:
                # Only update the counter the first time we find more than one entry
                duplicate_twitter_id_count += 1
        if positive_value_exists(one_organization.organization_twitter_handle):
            twitter_handle_string = str(one_organization.organization_twitter_handle)
            if twitter_handle_string in organizations_with_twitter_handle:
                organizations_with_twitter_handle[twitter_handle_string] += 1
            else:
                organizations_with_twitter_handle[twitter_handle_string] = 1
            if organizations_with_twitter_handle[twitter_handle_string] == 2:
                # Only update the counter the first time we find more than one entry
                duplicate_twitter_handle_count += 1

    # Cycle through the organizations again. If the organization has a unique twitter_id and twitter_handle,
    #  proceed with tests:
    #  *) Is there a voter who is linked to this same Twitter account?
    #    *) Is that voter linked to a different Organization? (If so: Merge them,
    #       If not: add voter.linked_organization_we_vote_id)
    #    *) Update voter's positions with organization ID
    #    *) Update organization's positions with voter ID
    organizations_with_a_twitter_collision = []
    organizations_with_unique_twitter_data = []
    organizations_with_correctly_linked_twitter_data = []
    organizations_with_unique_twitter_data_count = 0
    organizations_with_correctly_linked_twitter_data_count = 0
    organizations_without_twitter_data_count = 0
    twitter_link_mismatch_count = 0
    twitter_user_manager = TwitterUserManager()
    for one_organization in organization_list:
        unique_twitter_user_id_found = False
        twitter_id_collision_found = False
        unique_twitter_handle_found = False
        twitter_handle_collision_found = False
        if positive_value_exists(one_organization.twitter_user_id):
            twitter_user_id_string = str(one_organization.twitter_user_id)
            if twitter_user_id_string in organizations_with_twitter_id:
                if organizations_with_twitter_id[twitter_user_id_string] == 1:
                    unique_twitter_user_id_found = True
                elif organizations_with_twitter_id[twitter_user_id_string] > 1:
                    twitter_id_collision_found = True

        if positive_value_exists(one_organization.organization_twitter_handle):
            twitter_handle_string = str(one_organization.organization_twitter_handle)
            if twitter_handle_string in organizations_with_twitter_handle:
                if organizations_with_twitter_handle[twitter_handle_string] == 1:
                    unique_twitter_handle_found = True
                elif organizations_with_twitter_handle[twitter_handle_string] > 1:
                    twitter_handle_collision_found = True

        twitter_collision_found = twitter_id_collision_found or twitter_handle_collision_found

        if unique_twitter_user_id_found or unique_twitter_handle_found and not twitter_collision_found:
            # If here, we know we have an organization without multiple twitter ids or handles

            # Retrieve the linked_voter
            linked_voter_exists = False
            try:
                linked_voter = Voter.objects.get(
                    linked_organization_we_vote_id__iexact=one_organization.we_vote_id)
                one_organization.linked_voter = linked_voter
                linked_voter_exists = True
            except Voter.DoesNotExist:
                pass

            # Check to see if there is an existing TwitterLinkToOrganization
            if positive_value_exists(one_organization.twitter_user_id):
                try:
                    twitter_link_to_organization = TwitterLinkToOrganization.objects.get(
                        twitter_id=one_organization.twitter_user_id)
                    if positive_value_exists(twitter_link_to_organization.organization_we_vote_id):
                        one_organization.organization_we_vote_id_from_link_to_organization = \
                            twitter_link_to_organization.organization_we_vote_id
                        one_organization.twitter_id_from_link_to_organization = twitter_link_to_organization.twitter_id
                        # We reach out for the twitter_screen_name
                        one_organization.twitter_screen_name_from_link_to_organization = \
                            twitter_link_to_organization.fetch_twitter_handle_locally_or_remotely()
                except TwitterLinkToOrganization.DoesNotExist:
                    pass
            elif positive_value_exists(one_organization.organization_twitter_handle):
                twitter_user_manager = TwitterUserManager()
                twitter_results = twitter_user_manager.retrieve_twitter_user_locally_or_remotely(
                    0, one_organization.organization_twitter_handle)
                if twitter_results['twitter_user_found']:
                    twitter_user = twitter_results['twitter_user']
                    twitter_id = twitter_user.twitter_id
                    try:
                        twitter_link_to_organization = TwitterLinkToOrganization.objects.get(twitter_id=twitter_id)
                        if positive_value_exists(twitter_link_to_organization.organization_we_vote_id):
                            one_organization.organization_we_vote_id_from_link_to_organization = \
                                twitter_link_to_organization.organization_we_vote_id
                            one_organization.twitter_id_from_link_to_organization = \
                                twitter_link_to_organization.twitter_id
                            one_organization.twitter_screen_name_from_link_to_organization = twitter_user.twitter_handle
                    except TwitterLinkToOrganization.DoesNotExist:
                        pass

            # Check to see if there is an existing TwitterLinkToVoter
            if positive_value_exists(one_organization.twitter_user_id):
                try:
                    twitter_link_to_voter = TwitterLinkToVoter.objects.get(
                        twitter_id=one_organization.twitter_user_id)
                    if positive_value_exists(twitter_link_to_voter.voter_we_vote_id):
                        one_organization.voter_we_vote_id_from_link_to_voter = twitter_link_to_voter.voter_we_vote_id
                        one_organization.twitter_id_from_link_to_voter = twitter_link_to_voter.twitter_id
                        # We reach out for the twitter_screen_name
                        one_organization.twitter_screen_name_from_link_to_voter = \
                            twitter_link_to_voter.fetch_twitter_handle_locally_or_remotely()
                except TwitterLinkToVoter.DoesNotExist:
                    pass
            elif positive_value_exists(one_organization.organization_twitter_handle):
                twitter_results = twitter_user_manager.retrieve_twitter_user_locally_or_remotely(
                    0, one_organization.organization_twitter_handle)
                if twitter_results['twitter_user_found']:
                    twitter_user = twitter_results['twitter_user']
                    twitter_id = twitter_user.twitter_id
                    try:
                        twitter_link_to_voter = TwitterLinkToVoter.objects.get(twitter_id=twitter_id)
                        if positive_value_exists(twitter_link_to_voter.voter_we_vote_id):
                            one_organization.voter_we_vote_id_from_link_to_voter = \
                                twitter_link_to_voter.voter_we_vote_id
                            one_organization.twitter_id_from_link_to_voter = twitter_link_to_voter.twitter_id
                            one_organization.twitter_screen_name_from_link_to_voter = twitter_user.twitter_handle
                    except TwitterLinkToVoter.DoesNotExist:
                        pass

            # Are there any data mismatches?  TODO DALE

            # Does TwitterLinkToOrganization exist? If so, does it match Twitter account in TwitterLinkToVoter?
            if hasattr(one_organization, 'twitter_id_from_link_to_voter') and \
                    positive_value_exists(one_organization.twitter_id_from_link_to_voter) and \
                    hasattr(one_organization, 'twitter_id_from_link_to_organization') and \
                    positive_value_exists(one_organization.twitter_id_from_link_to_organization):
                if one_organization.twitter_id_from_link_to_voter != \
                        one_organization.twitter_id_from_link_to_organization:
                    one_organization.twitter_link_mismatch = True
                    twitter_link_mismatch_count += 1
            elif hasattr(one_organization, 'twitter_id_from_link_to_voter') and \
                    positive_value_exists(one_organization.twitter_id_from_link_to_voter) and \
                    positive_value_exists(one_organization.twitter_user_id):
                if one_organization.twitter_id_from_link_to_voter != \
                        one_organization.twitter_user_id:
                    one_organization.twitter_link_mismatch = True
                    twitter_link_mismatch_count += 1
            elif hasattr(one_organization, 'twitter_screen_name_from_link_to_voter') and \
                    positive_value_exists(one_organization.twitter_screen_name_from_link_to_voter) and \
                    positive_value_exists(one_organization.organization_twitter_handle):
                if one_organization.twitter_screen_name_from_link_to_voter != \
                        one_organization.organization_twitter_handle:
                    one_organization.twitter_link_mismatch = True
                    twitter_link_mismatch_count += 1

            # If there isn't a Twitter link mismatch, and create_twitter_link_to_organization is True, do it
            if create_twitter_link_to_organization \
                    and not hasattr(one_organization, 'twitter_id_from_link_to_organization') \
                    and not hasattr(one_organization, 'twitter_link_mismatch'):
                twitter_user_manager = TwitterUserManager()
                twitter_id_to_create = one_organization.twitter_user_id
                if positive_value_exists(one_organization.organization_twitter_handle) \
                        and not positive_value_exists(twitter_id_to_create):
                    twitter_results = twitter_user_manager.retrieve_twitter_user_locally_or_remotely(
                        0, one_organization.organization_twitter_handle)
                    if twitter_results['twitter_user_found']:
                        twitter_user = twitter_results['twitter_user']
                        twitter_id_to_create = twitter_user.twitter_id

                results = twitter_user_manager.create_twitter_link_to_organization(
                    twitter_id_to_create, one_organization.we_vote_id)
                if results['twitter_link_to_organization_saved']:
                    twitter_link_to_organization = results['twitter_link_to_organization']
                    one_organization.organization_we_vote_id_from_link_to_organization = \
                        twitter_link_to_organization.organization_we_vote_id
                    one_organization.twitter_id_from_link_to_organization = \
                        twitter_link_to_organization.twitter_id
                    one_organization.twitter_screen_name_from_link_to_organization = \
                        twitter_link_to_organization.fetch_twitter_handle_locally_or_remotely()

            if hasattr(one_organization, 'twitter_id_from_link_to_voter') and \
                    positive_value_exists(one_organization.twitter_id_from_link_to_voter) and \
                    hasattr(one_organization, 'twitter_id_from_link_to_organization') and \
                    positive_value_exists(one_organization.twitter_id_from_link_to_organization) and \
                    one_organization.twitter_id_from_link_to_voter == \
                    one_organization.twitter_id_from_link_to_organization:
                organizations_with_correctly_linked_twitter_data.append(one_organization)
                organizations_with_correctly_linked_twitter_data_count += 1
            else:
                organizations_with_unique_twitter_data.append(one_organization)
                organizations_with_unique_twitter_data_count += 1
        elif twitter_collision_found:
            organizations_with_a_twitter_collision.append(one_organization)
        elif not (unique_twitter_user_id_found or unique_twitter_handle_found):
            organizations_without_twitter_data_count += 1

    org_list_analysis_message = ""
    org_list_analysis_message += "duplicate_twitter_id_count: " + \
                                 str(duplicate_twitter_id_count) + "<br />"
    org_list_analysis_message += "duplicate_twitter_handle_count: " + \
                                 str(duplicate_twitter_handle_count) + "<br />"
    org_list_analysis_message += "organizations_with_correctly_linked_twitter_data_count: " + \
                                 str(organizations_with_correctly_linked_twitter_data_count) + "<br />"
    org_list_analysis_message += "organizations_with_unique_twitter_data_count: " + \
                                 str(organizations_with_unique_twitter_data_count) + "<br />"
    org_list_analysis_message += "organizations_without_twitter_data_count: " + \
                                 str(organizations_without_twitter_data_count) + "<br />"
    org_list_analysis_message += "twitter_link_mismatch_count: " + \
                                 str(twitter_link_mismatch_count) + "<br />"

    messages.add_message(request, messages.INFO, org_list_analysis_message)

    messages_on_stage = get_messages(request)

    template_values = {
        'messages_on_stage':                        messages_on_stage,
        'organizations_with_correctly_linked_twitter_data': organizations_with_correctly_linked_twitter_data,
        'organizations_with_unique_twitter_data':   organizations_with_unique_twitter_data,
        'organizations_with_a_twitter_collision':   organizations_with_a_twitter_collision,
    }
    response = render(request, 'admin_tools/data_cleanup_organization_list_analysis.html', template_values)

    return response


@login_required
def data_cleanup_position_list_analysis_view(request):
    authority_required = {'admin'}  # admin, verified_volunteer
    if not voter_has_authority(request, authority_required):
        return redirect_to_sign_in_page(request, authority_required)

    organization_we_vote_id_added = 0
    organization_we_vote_id_added_failed = 0
    voter_we_vote_id_added = 0
    voter_we_vote_id_added_failed = 0

    google_civic_election_id = convert_to_int(request.GET.get('google_civic_election_id', 0))
    election_list = Election.objects.order_by('-election_day_text')

    # PositionEntered: Find Positions that have an organization_id but not an organization_we_vote_id and repair
    public_positions_with_organization_id_only = PositionEntered.objects.all()
    if positive_value_exists(google_civic_election_id):
        public_positions_with_organization_id_only = public_positions_with_organization_id_only.filter(
            google_civic_election_id=google_civic_election_id)
    public_positions_with_organization_id_only = \
        public_positions_with_organization_id_only.filter(organization_id__gt=0)
    public_positions_with_organization_id_only = public_positions_with_organization_id_only.filter(
        Q(organization_we_vote_id=None) | Q(organization_we_vote_id=""))
    # We limit these to 20 since we are doing other lookup
    public_positions_with_organization_id_only = public_positions_with_organization_id_only[:20]
    for one_position in public_positions_with_organization_id_only:
        organization_manager = OrganizationManager()
        results = organization_manager.retrieve_organization_from_id(one_position.organization_id)
        if results['organization_found']:
            try:
                organization = results['organization']
                one_position.organization_we_vote_id = organization.we_vote_id
                one_position.save()
                organization_we_vote_id_added += 1
            except Exception as e:  # Look for positions where:
                organization_we_vote_id_added_failed += 1

    # PositionForFriends: Find Positions that have an organization_id but not an organization_we_vote_id and repair
    public_positions_with_organization_id_only = PositionForFriends.objects.all()
    if positive_value_exists(google_civic_election_id):
        public_positions_with_organization_id_only = public_positions_with_organization_id_only.filter(
            google_civic_election_id=google_civic_election_id)
    public_positions_with_organization_id_only = \
        public_positions_with_organization_id_only.filter(organization_id__gt=0)
    public_positions_with_organization_id_only = public_positions_with_organization_id_only.filter(
        Q(organization_we_vote_id=None) | Q(organization_we_vote_id=""))
    # We limit these to 20 since we are doing other lookup
    public_positions_with_organization_id_only = public_positions_with_organization_id_only[:20]
    for one_position in public_positions_with_organization_id_only:
        organization_manager = OrganizationManager()
        results = organization_manager.retrieve_organization_from_id(one_position.organization_id)
        if results['organization_found']:
            try:
                organization = results['organization']
                one_position.organization_we_vote_id = organization.we_vote_id
                one_position.save()
                organization_we_vote_id_added += 1
            except Exception as e:  # Look for positions where:
                organization_we_vote_id_added_failed += 1

    # PositionEntered: Find Positions that have a voter_id but not a voter_we_vote_id
    public_positions_with_voter_id_only = PositionEntered.objects.all()
    if positive_value_exists(google_civic_election_id):
        public_positions_with_voter_id_only = public_positions_with_voter_id_only.filter(
            google_civic_election_id=google_civic_election_id)
    public_positions_with_voter_id_only = \
        public_positions_with_voter_id_only.filter(voter_id__gt=0)
    public_positions_with_voter_id_only = public_positions_with_voter_id_only.filter(
        Q(voter_we_vote_id=None) | Q(voter_we_vote_id=""))
    # We limit these to 20 since we are doing other lookup
    public_positions_with_voter_id_only = public_positions_with_voter_id_only[:20]
    for one_position in public_positions_with_voter_id_only:
        voter_manager = VoterManager()
        results = voter_manager.retrieve_voter_by_id(one_position.voter_id)
        if results['voter_found']:
            try:
                voter = results['voter']
                one_position.voter_we_vote_id = voter.we_vote_id
                one_position.save()
                voter_we_vote_id_added += 1
            except Exception as e:  # Look for positions where:
                voter_we_vote_id_added_failed += 1

    # PositionForFriends: Find Positions that have a voter_id but not a voter_we_vote_id
    public_positions_with_voter_id_only = PositionForFriends.objects.all()
    if positive_value_exists(google_civic_election_id):
        public_positions_with_voter_id_only = public_positions_with_voter_id_only.filter(
            google_civic_election_id=google_civic_election_id)
    public_positions_with_voter_id_only = \
        public_positions_with_voter_id_only.filter(voter_id__gt=0)
    public_positions_with_voter_id_only = public_positions_with_voter_id_only.filter(
        Q(voter_we_vote_id=None) | Q(voter_we_vote_id=""))
    # We limit these to 20 since we are doing other lookup
    public_positions_with_voter_id_only = public_positions_with_voter_id_only[:20]
    for one_position in public_positions_with_voter_id_only:
        voter_manager = VoterManager()
        results = voter_manager.retrieve_voter_by_id(one_position.voter_id)
        if results['voter_found']:
            try:
                voter = results['voter']
                one_position.voter_we_vote_id = voter.we_vote_id
                one_position.save()
                voter_we_vote_id_added += 1
            except Exception as e:  # Look for positions where:
                voter_we_vote_id_added_failed += 1

    # *) voter_we_vote_id doesn't match organization_we_vote_id
    # *) In public position table, an organization_we_vote_id doesn't exist

    # These are Positions that should have an organization_we_vote_id but do not
    #  We know they should have a organization_we_vote_id because they are in the PositionEntered table
    public_positions_without_organization = PositionEntered.objects.all()
    if positive_value_exists(google_civic_election_id):
        public_positions_without_organization = public_positions_without_organization.filter(
            google_civic_election_id=google_civic_election_id)
    public_positions_without_organization = public_positions_without_organization.filter(
        Q(organization_we_vote_id=None) | Q(organization_we_vote_id=""))
    # We limit these to 20 since we are doing other lookup
    public_positions_without_organization = public_positions_without_organization[:20]

    # PositionsForFriends without organization_we_vote_id
    positions_for_friends_without_organization = PositionForFriends.objects.all()
    if positive_value_exists(google_civic_election_id):
        positions_for_friends_without_organization = positions_for_friends_without_organization.filter(
            google_civic_election_id=google_civic_election_id)
    positions_for_friends_without_organization = positions_for_friends_without_organization.filter(
        Q(organization_we_vote_id=None) | Q(organization_we_vote_id=""))
    # We limit these to 20 since we are doing other lookup
    positions_for_friends_without_organization = positions_for_friends_without_organization[:20]

    position_list_analysis_message = ""
    position_list_analysis_message += "organization_we_vote_id_added: " + \
                                      str(organization_we_vote_id_added) + "<br />"
    position_list_analysis_message += "organization_we_vote_id_added_failed: " + \
                                      str(organization_we_vote_id_added_failed) + "<br />"
    position_list_analysis_message += "voter_we_vote_id_added: " + \
                                      str(voter_we_vote_id_added) + "<br />"
    position_list_analysis_message += "voter_we_vote_id_added_failed: " + \
                                      str(voter_we_vote_id_added_failed) + "<br />"

    messages.add_message(request, messages.INFO, position_list_analysis_message)

    messages_on_stage = get_messages(request)

    template_values = {
        'messages_on_stage':                            messages_on_stage,
        'election_list':                                election_list,
        'google_civic_election_id':                     google_civic_election_id,
        'public_positions_without_organization':        public_positions_without_organization,
        'positions_for_friends_without_organization':   positions_for_friends_without_organization,
        'public_positions_with_organization_id_only':   public_positions_with_organization_id_only,
    }
    response = render(request, 'admin_tools/data_cleanup_position_list_analysis.html', template_values)

    return response


@login_required
def data_cleanup_voter_hanging_data_process_view(request):
    authority_required = {'admin'}  # admin, verified_volunteer
    if not voter_has_authority(request, authority_required):
        return redirect_to_sign_in_page(request, authority_required)

    # Find any Voter entries where there is any email data, and then below check the EmailAddress table
    #  to see if there is an entry
    voter_hanging_email_ownership_is_verified_list = Voter.objects.all()
    voter_hanging_email_ownership_is_verified_list = voter_hanging_email_ownership_is_verified_list.filter(
        email_ownership_is_verified=True)
    voter_hanging_email_ownership_is_verified_list = voter_hanging_email_ownership_is_verified_list

    voter_email_print_list = ""
    email_ownership_is_verified_set_to_false_count = 0
    voter_emails_cleared_out = 0
    primary_email_not_found = 0
    primary_email_not_found_note = ""
    verified_email_addresses = 0
    voter_owner_of_email_not_found = 0
    voter_owner_new_primary_email_saved = 0
    voter_owner_new_primary_email_failed = 0
    voter_owner_new_primary_email_failed_note = ""
    for one_voter in voter_hanging_email_ownership_is_verified_list:
        # Clean if it is the simplest case of email_ownership_is_verified, but email and primary_email_we_vote_id None
        # voter_email_print_list += "voter.email: " + str(one_voter.email) + ", "
        # voter_email_print_list += "voter.primary_email_we_vote_id: " + str(one_voter.primary_email_we_vote_id) + ", "
        # voter_email_print_list += "voter.email_ownership_is_verified: " + str(one_voter.email_ownership_is_verified)
        # voter_email_print_list += " :: <br />"
        if positive_value_exists(one_voter.email_ownership_is_verified) and \
                one_voter.email is None and \
                one_voter.primary_email_we_vote_id is None:
            # If here, then the voter entry incorrectly thinks it has a verified email saved
            one_voter.email_ownership_is_verified = False
            one_voter.save()
            email_ownership_is_verified_set_to_false_count += 1
        elif positive_value_exists(one_voter.email_ownership_is_verified) and \
                one_voter.email is not None and \
                one_voter.primary_email_we_vote_id is None:
            # If here, an email value exists, but we don't have a primary_email_we_vote_id listed
            # Check to see if the email can be made the primary TODO DALE
            pass
        elif positive_value_exists(one_voter.primary_email_we_vote_id):
            # Is there an EmailAddress entry matching this primary_email_we_vote_id?
            try:
                verified_email_address = EmailAddress.objects.get(
                    we_vote_id=one_voter.primary_email_we_vote_id,
                    email_ownership_is_verified=True,
                    # email_permanent_bounce=False,
                    deleted=False
                )
                # Does this master EmailAddress entry agree that this voter owns this email
                if verified_email_address.voter_we_vote_id == one_voter.we_vote_id:
                    # Make sure the cached email address matches
                    if one_voter.email != verified_email_address.normalized_email_address:
                        try:
                            one_voter.email = verified_email_address.normalized_email_address
                            one_voter.save()
                        except Exception as e:
                            pass
                else:
                    # Clear out this email from the voter table
                    try:
                        one_voter.email = None
                        one_voter.primary_email_we_vote_id = None
                        one_voter.email_ownership_is_verified = False
                        one_voter.save()
                        voter_emails_cleared_out += 1
                    except Exception as e:
                        pass
            except EmailAddress.DoesNotExist:
                # primary_email_we_vote_id could not be found, so we may need to clear out this email from  voter table
                primary_email_not_found += 1
                # primary_email_not_found_note += one_voter.primary_email_we_vote_id + " "
                try:
                    one_voter.email = None
                    one_voter.primary_email_we_vote_id = None
                    one_voter.email_ownership_is_verified = False
                    one_voter.save()
                except Exception as e:
                    pass

    # Go through all of the verified email addresses in the EmailAddress table and make sure the
    # cached information is up-to-date in the voter table
    email_address_verified_list = EmailAddress.objects.all()
    email_address_verified_list = email_address_verified_list.filter(email_ownership_is_verified=True)
    for one_email in email_address_verified_list:
        if positive_value_exists(one_email.voter_we_vote_id):
            verified_email_addresses += 1
            try:
                voter_owner_of_email = Voter.objects.get(we_vote_id__iexact=one_email.voter_we_vote_id)
                # Does this voter already have a primary email address?
                if positive_value_exists(voter_owner_of_email.primary_email_we_vote_id):
                    # Leave it in place
                    pass
                else:
                    # Otherwise save the first email for this person as the primary
                    try:
                        voter_owner_of_email.email = one_email.normalized_email_address
                        voter_owner_of_email.primary_email_we_vote_id = one_email.we_vote_id
                        voter_owner_of_email.email_ownership_is_verified = True
                        voter_owner_of_email.save()
                        voter_owner_new_primary_email_saved += 1
                    except Exception as e:
                        voter_owner_new_primary_email_failed += 1
                        voter_owner_new_primary_email_failed_note += one_email.we_vote_id + " "
            except Exception as e:
                voter_owner_of_email_not_found += 1

    voter_email_print_list += "email_ownership_is_verified, reset to False: " + \
                              str(email_ownership_is_verified_set_to_false_count) + " <br />"
    voter_email_print_list += "voter_emails_cleared_out: " + \
                              str(voter_emails_cleared_out) + " <br />"
    voter_email_print_list += "primary_email_not_found: " + \
                              str(primary_email_not_found) + " " + primary_email_not_found_note + "<br />"
    voter_email_print_list += "verified_email_addresses: " + \
                              str(verified_email_addresses) + "<br />"
    voter_email_print_list += "voter_owner_of_email_not_found: " + \
                              str(voter_owner_of_email_not_found) + "<br />"
    voter_email_print_list += "voter_owner_new_primary_email_saved: " + \
                              str(voter_owner_new_primary_email_saved) + "<br />"
    voter_email_print_list += "voter_owner_new_primary_email_failed: " + \
                              str(voter_owner_new_primary_email_failed) + " " + \
                              voter_owner_new_primary_email_failed_note + "<br />"

    messages.add_message(request, messages.INFO, voter_email_print_list)

    return HttpResponseRedirect(reverse('admin_tools:data_cleanup', args=()))


@login_required
def data_cleanup_voter_list_analysis_view(request):
    authority_required = {'admin'}  # admin, verified_volunteer
    if not voter_has_authority(request, authority_required):
        return redirect_to_sign_in_page(request, authority_required)

    create_facebook_link_to_voter = request.GET.get('create_facebook_link_to_voter', False)
    create_twitter_link_to_voter = request.GET.get('create_twitter_link_to_voter', False)
    updated_suggested_friends = request.GET.get('updated_suggested_friends', False)

    status_print_list = ""
    create_facebook_link_to_voter_possible = 0
    create_facebook_link_to_voter_added = 0
    create_facebook_link_to_voter_not_added = 0
    create_twitter_link_to_voter_possible = 0
    create_twitter_link_to_voter_added = 0
    create_twitter_link_to_voter_not_added = 0
    voter_address_manager = VoterAddressManager()
    facebook_manager = FacebookManager()
    twitter_user_manager = TwitterUserManager()
    friend_manager = FriendManager()

    suggested_friend_created_count = 0
    if updated_suggested_friends:
        current_friends_results = CurrentFriend.objects.all()

        for one_current_friend in current_friends_results:
            # Start with one_current_friend.viewer_voter_we_vote_id, get a list of all of that voter's friends
            results = friend_manager.update_suggested_friends_starting_with_one_voter(
                one_current_friend.viewer_voter_we_vote_id)
            if results['suggested_friend_created_count']:
                suggested_friend_created_count += results['suggested_friend_created_count']

            # Then do the other side of the friendship - the viewee
            results = friend_manager.update_suggested_friends_starting_with_one_voter(
                one_current_friend.viewee_voter_we_vote_id)
            if results['suggested_friend_created_count']:
                suggested_friend_created_count += results['suggested_friend_created_count']

    voter_list_with_local_twitter_data = Voter.objects.order_by('-id', '-date_last_changed')
    voter_list_with_local_twitter_data = voter_list_with_local_twitter_data.filter(
        ~Q(twitter_id=None) | ~Q(twitter_screen_name=None) | ~Q(email=None) | ~Q(facebook_id=None) |
        ~Q(fb_username=None) | ~Q(linked_organization_we_vote_id=None))
    voter_list_with_local_twitter_data = voter_list_with_local_twitter_data

    voter_list_with_local_twitter_data_updated = []
    number_of_voters_found = 0
    for one_linked_voter in voter_list_with_local_twitter_data:
        number_of_voters_found += 1

        one_linked_voter.text_for_map_search = \
            voter_address_manager.retrieve_text_for_map_search_from_voter_id(one_linked_voter.id)

        # Get FacebookLinkToVoter
        facebook_id_from_link_to_voter = 0
        try:
            facebook_link_to_voter = FacebookLinkToVoter.objects.get(
                voter_we_vote_id__iexact=one_linked_voter.we_vote_id)
            if positive_value_exists(facebook_link_to_voter.facebook_user_id):
                facebook_id_from_link_to_voter = facebook_link_to_voter.facebook_user_id
                one_linked_voter.facebook_id_from_link_to_voter = facebook_link_to_voter.facebook_user_id
        except FacebookLinkToVoter.DoesNotExist:
            pass

        # Get TwitterLinkToVoter
        twitter_id_from_link_to_voter = 0
        try:
            twitter_link_to_voter = TwitterLinkToVoter.objects.get(
                voter_we_vote_id__iexact=one_linked_voter.we_vote_id)
            if positive_value_exists(twitter_link_to_voter.twitter_id):
                twitter_id_from_link_to_voter = twitter_link_to_voter.twitter_id
                one_linked_voter.twitter_id_from_link_to_voter = twitter_link_to_voter.twitter_id
                # We reach out for the twitter_screen_name
                one_linked_voter.twitter_screen_name_from_link_to_voter = \
                    twitter_link_to_voter.fetch_twitter_handle_locally_or_remotely()
        except TwitterLinkToVoter.DoesNotExist:
            pass

        # Get TwitterLinkToOrganization
        try:
            one_linked_voter.twitter_link_to_organization_status = ""
            if positive_value_exists(twitter_id_from_link_to_voter):
                twitter_id_to_search = twitter_id_from_link_to_voter
                twitter_link_to_organization_twitter_id_source_text = "FROM TW_LINK_TO_VOTER"
            else:
                twitter_id_to_search = one_linked_voter.twitter_id
                twitter_link_to_organization_twitter_id_source_text = "FROM VOTER RECORD"

            if positive_value_exists(twitter_id_to_search):
                twitter_link_to_organization = TwitterLinkToOrganization.objects.get(
                    twitter_id=twitter_id_to_search)
                if positive_value_exists(twitter_link_to_organization.twitter_id):
                    one_linked_voter.organization_we_vote_id_from_link_to_organization = \
                        twitter_link_to_organization.organization_we_vote_id
                    one_linked_voter.twitter_id_from_link_to_organization = twitter_link_to_organization.twitter_id
                    # We reach out for the twitter_screen_name
                    one_linked_voter.twitter_screen_name_from_link_to_organization = \
                        twitter_link_to_organization.fetch_twitter_handle_locally_or_remotely()
                    one_linked_voter.twitter_link_to_organization_twitter_id_source_text = \
                        twitter_link_to_organization_twitter_id_source_text
        except TwitterLinkToOrganization.DoesNotExist:
            pass

        # Do any other voters have this Facebook data? If not, we can create a FacebookLinkToVoter entry.
        duplicate_facebook_data_found = False
        voter_raw_filters = []
        if positive_value_exists(one_linked_voter.facebook_id):
            new_voter_filter = Q(facebook_id=one_linked_voter.facebook_id)
            voter_raw_filters.append(new_voter_filter)

        if len(voter_raw_filters):
            final_voter_filters = voter_raw_filters.pop()

            # ...and "OR" the remaining items in the list
            for item in voter_raw_filters:
                final_voter_filters |= item

            duplicate_facebook_data_voter_list = Voter.objects.all()
            duplicate_facebook_data_voter_list = duplicate_facebook_data_voter_list.filter(final_voter_filters)
            duplicate_facebook_data_voter_list = duplicate_facebook_data_voter_list.exclude(
                we_vote_id__iexact=one_linked_voter.we_vote_id)
            duplicate_facebook_data_found = positive_value_exists(len(duplicate_facebook_data_voter_list))
            one_linked_voter.duplicate_facebook_data_found = duplicate_facebook_data_found

        if facebook_id_from_link_to_voter or duplicate_facebook_data_found:
            # Do not offer the create_facebook_link
            pass
        else:
            if positive_value_exists(one_linked_voter.facebook_id):
                if create_facebook_link_to_voter:
                    # If here, we want to create a FacebookLinkToVoter
                    create_results = facebook_manager.create_facebook_link_to_voter(one_linked_voter.facebook_id,
                                                                                    one_linked_voter.we_vote_id)
                    if positive_value_exists(create_results['facebook_link_to_voter_saved']):
                        create_facebook_link_to_voter_added += 1
                        facebook_link_to_voter = create_results['facebook_link_to_voter']
                        if positive_value_exists(facebook_link_to_voter.facebook_user_id):
                            one_linked_voter.facebook_id_from_link_to_voter = facebook_link_to_voter.facebook_user_id
                    else:
                        create_facebook_link_to_voter_not_added += 1
                else:
                    create_facebook_link_to_voter_possible += 1

        # Do any other voters have this Twitter data? If not, we can create a TwitterLinkToVoter entry.
        duplicate_twitter_data_found = False
        voter_raw_filters = []
        if positive_value_exists(one_linked_voter.twitter_id):
            new_voter_filter = Q(twitter_id=one_linked_voter.twitter_id)
            voter_raw_filters.append(new_voter_filter)
        if positive_value_exists(one_linked_voter.twitter_screen_name):
            new_voter_filter = Q(twitter_screen_name__iexact=one_linked_voter.twitter_screen_name)
            voter_raw_filters.append(new_voter_filter)

        if len(voter_raw_filters):
            final_voter_filters = voter_raw_filters.pop()

            # ...and "OR" the remaining items in the list
            for item in voter_raw_filters:
                final_voter_filters |= item

            duplicate_twitter_data_voter_list = Voter.objects.all()
            duplicate_twitter_data_voter_list = duplicate_twitter_data_voter_list.filter(final_voter_filters)
            duplicate_twitter_data_voter_list = duplicate_twitter_data_voter_list.exclude(
                we_vote_id__iexact=one_linked_voter.we_vote_id)
            duplicate_twitter_data_found = positive_value_exists(len(duplicate_twitter_data_voter_list))
            one_linked_voter.duplicate_twitter_data_found = duplicate_twitter_data_found

        if twitter_id_from_link_to_voter or duplicate_twitter_data_found:
            # Do not offer the create_twitter_link
            pass
        else:
            if positive_value_exists(one_linked_voter.twitter_id) \
                    or positive_value_exists(one_linked_voter.twitter_screen_name):
                if create_twitter_link_to_voter:
                    # If here, we want to create a TwitterLinkToVoter
                    create_results = twitter_user_manager.create_twitter_link_to_voter(one_linked_voter.twitter_id,
                                                                                       one_linked_voter.we_vote_id)
                    if positive_value_exists(create_results['twitter_link_to_voter_saved']):
                        create_twitter_link_to_voter_added += 1
                        twitter_link_to_voter = create_results['twitter_link_to_voter']
                        if positive_value_exists(twitter_link_to_voter.twitter_id):
                            one_linked_voter.twitter_id_from_link_to_voter = twitter_link_to_voter.twitter_id
                            # We reach out for the twitter_screen_name
                            one_linked_voter.twitter_screen_name_from_link_to_voter = \
                                twitter_link_to_voter.fetch_twitter_handle_locally_or_remotely()
                            one_linked_voter.twitter_link_to_organization_twitter_id_source_text = " JUST ALTERED"
                    else:
                        create_twitter_link_to_voter_not_added += 1
                else:
                    create_twitter_link_to_voter_possible += 1

        one_linked_voter.links_to_other_organizations = \
            find_organizations_referenced_in_positions_for_this_voter(one_linked_voter)
        one_linked_voter.positions_count = fetch_positions_count_for_this_voter(one_linked_voter)

        email_address_list = EmailAddress.objects.all()
        email_address_list = email_address_list.filter(voter_we_vote_id__iexact=one_linked_voter.we_vote_id)
        one_linked_voter.linked_emails = email_address_list

        # Friend statistics
        one_linked_voter.current_friends_count = \
            friend_manager.fetch_current_friends_count(one_linked_voter.we_vote_id)
        one_linked_voter.friend_invitations_sent_by_me_count = \
            friend_manager.fetch_friend_invitations_sent_by_me_count(one_linked_voter.we_vote_id)
        one_linked_voter.friend_invitations_sent_to_me_count = \
            friend_manager.fetch_friend_invitations_sent_to_me_count(one_linked_voter.we_vote_id)
        one_linked_voter.suggested_friend_list_count = \
            friend_manager.fetch_suggested_friends_count(one_linked_voter.we_vote_id)
        follow_list_manager = FollowOrganizationList()
        one_linked_voter.organizations_followed_count = \
            follow_list_manager.fetch_follow_organization_by_voter_id_count(one_linked_voter.id)

        voter_list_with_local_twitter_data_updated.append(one_linked_voter)

    status_print_list += "create_facebook_link_to_voter_possible: " + \
                         str(create_facebook_link_to_voter_possible) + ", "
    if positive_value_exists(create_facebook_link_to_voter_added):
        status_print_list += "create_facebook_link_to_voter_added: " + \
                             str(create_facebook_link_to_voter_added) + "<br />"
    if positive_value_exists(create_facebook_link_to_voter_not_added):
        status_print_list += "create_facebook_link_to_voter_not_added: " + \
                             str(create_facebook_link_to_voter_not_added) + "<br />"
    status_print_list += "create_twitter_link_to_voter_possible: " + \
                         str(create_twitter_link_to_voter_possible) + ", "
    if positive_value_exists(create_twitter_link_to_voter_added):
        status_print_list += "create_twitter_link_to_voter_added: " + \
                             str(create_twitter_link_to_voter_added) + "<br />"
    if positive_value_exists(create_twitter_link_to_voter_not_added):
        status_print_list += "create_twitter_link_to_voter_not_added: " + \
                             str(create_twitter_link_to_voter_not_added) + "<br />"
    status_print_list += "number_of_voters_found: " + \
                         str(number_of_voters_found) + "<br />"
    if positive_value_exists(suggested_friend_created_count):
        status_print_list += "suggested_friend_created_count: " + \
                             str(suggested_friend_created_count) + "<br />"

    messages.add_message(request, messages.INFO, status_print_list)

    messages_on_stage = get_messages(request)

    template_values = {
        'messages_on_stage':                        messages_on_stage,
        'voter_list_with_local_twitter_data':       voter_list_with_local_twitter_data_updated,
    }
    response = render(request, 'admin_tools/data_cleanup_voter_list_analysis.html', template_values)

    return response


@login_required
def delete_test_data_view(request):
    authority_required = {'admin'}  # admin, verified_volunteer
    if not voter_has_authority(request, authority_required):
        return redirect_to_sign_in_page(request, authority_required)

    # We leave in place the polling locations data and the election data from Google civic

    # Delete candidate data from exported file

    # Delete organization data from exported file

    # Delete positions data from exported file
    return HttpResponseRedirect(reverse('admin_tools:admin_home', args=()))


@login_required
def import_sample_data_view(request):
    authority_required = {'admin'}  # admin, verified_volunteer
    if not voter_has_authority(request, authority_required):
        return redirect_to_sign_in_page(request, authority_required)

    # This routine works without requiring a Google Civic API key

    # We want to make sure that all voters have been updated to have a we_vote_id
    voter_list = Voter.objects.all()
    for one_voter in voter_list:
        one_voter.save()

    polling_locations_results = import_and_save_all_polling_locations_data()

    # NOTE: The approach of having each developer pull directly from Google Civic won't work because if we are going
    # to import positions, we need to have stable we_vote_ids for all ballot items
    # =========================
    # # We redirect to the view that calls out to Google Civic and brings in ballot data
    # # This isn't ideal (I'd rather call a controller instead of redirecting to a view), but this is a unique case
    # # and we have a lot of error-display-to-screen code
    # election_local_id = 0
    # google_civic_election_id = 4162  # Virginia
    # return HttpResponseRedirect(reverse('election:election_all_ballots_retrieve',
    #                                     args=(election_local_id,)) +
    #                             "?google_civic_election_id=" + str(google_civic_election_id))

    # Import election data from We Vote export file
    elections_results = elections_import_from_sample_file()

    # Import ContestOffices
    load_from_uri = False
    offices_results = offices_import_from_sample_file(request, load_from_uri)

    # Import candidate data from We Vote export file
    load_from_uri = False
    candidates_results = candidates_import_from_sample_file(request, load_from_uri)

    # Import ContestMeasures

    # Import organization data from We Vote export file
    load_from_uri = False
    organizations_results = organizations_import_from_sample_file(request, load_from_uri)

    # Import positions data from We Vote export file
    # load_from_uri = False
    positions_results = positions_import_from_sample_file(request)  # , load_from_uri

    messages.add_message(request, messages.INFO,
                         'The following data has been imported: <br />'
                         'Polling locations saved: {polling_locations_saved}, updated: {polling_locations_updated},'
                         ' not_processed: {polling_locations_not_processed} <br />'
                         'Elections saved: {elections_saved}, updated: {elections_updated},'
                         ' not_processed: {elections_not_processed} <br />'
                         'Offices saved: {offices_saved}, updated: {offices_updated},'
                         ' not_processed: {offices_not_processed} <br />'
                         'Candidates saved: {candidates_saved}, updated: {candidates_updated},'
                         ' not_processed: {candidates_not_processed} <br />'
                         'Organizations saved: {organizations_saved}, updated: {organizations_updated},'
                         ' not_processed: {organizations_not_processed} <br />'
                         'Positions saved: {positions_saved}, updated: {positions_updated},'
                         ' not_processed: {positions_not_processed} <br />'
                         ''.format(
                             polling_locations_saved=polling_locations_results['saved'],
                             polling_locations_updated=polling_locations_results['updated'],
                             polling_locations_not_processed=polling_locations_results['not_processed'],
                             elections_saved=elections_results['saved'],
                             elections_updated=elections_results['updated'],
                             elections_not_processed=elections_results['not_processed'],
                             offices_saved=offices_results['saved'],
                             offices_updated=offices_results['updated'],
                             offices_not_processed=offices_results['not_processed'],
                             candidates_saved=candidates_results['saved'],
                             candidates_updated=candidates_results['updated'],
                             candidates_not_processed=candidates_results['not_processed'],
                             organizations_saved=organizations_results['saved'],
                             organizations_updated=organizations_results['updated'],
                             organizations_not_processed=organizations_results['not_processed'],
                             positions_saved=positions_results['saved'],
                             positions_updated=positions_results['updated'],
                             positions_not_processed=positions_results['not_processed'],
                         ))
    return HttpResponseRedirect(reverse('admin_tools:admin_home', args=()))


def login_user(request):
    """
    This method is called when you login from the /login/ form
    :param request:
    :return:
    """
    voter_api_device_id = get_voter_api_device_id(request)  # We look in the cookies for voter_api_device_id
    store_new_voter_api_device_id_in_cookie = False
    voter_signed_in = False

    voter_manager = VoterManager()
    voter_device_link_manager = VoterDeviceLinkManager()
    results = voter_manager.retrieve_voter_from_voter_device_id(voter_api_device_id)
    if results['voter_found']:
        voter_on_stage = results['voter']
        voter_on_stage_id = voter_on_stage.id
        # Just because a We Vote voter is found doesn't mean they are authenticated for Django
    else:
        voter_on_stage_id = 0

    info_message = ''
    error_message = ''
    username = ''

    # Does Django think user is already signed in?
    if request.user.is_authenticated():
        # If so, make sure user and voter_on_stage are the same.
        if request.user.id != voter_on_stage_id:
            # Delete the prior voter_api_device_id from database
            voter_device_link_manager.delete_voter_device_link(voter_api_device_id)

            # Create a new voter_api_device_id and voter_device_link
            voter_api_device_id = generate_voter_device_id()
            results = voter_device_link_manager.save_new_voter_device_link(voter_api_device_id, request.user.id)
            store_new_voter_api_device_id_in_cookie = results['voter_device_link_created']
            voter_on_stage = request.user
            voter_on_stage_id = voter_on_stage.id
    elif request.POST:
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(username=username, password=password)
        if user is not None:
            if user.is_active:
                login(request, user)
                info_message = "You're successfully logged in!"

                # Delete the prior voter_api_device_id from database
                voter_device_link_manager.delete_voter_device_link(voter_api_device_id)

                # Create a new voter_api_device_id and voter_device_link
                voter_api_device_id = generate_voter_device_id()
                results = voter_device_link_manager.save_new_voter_device_link(voter_api_device_id, user.id)
                store_new_voter_api_device_id_in_cookie = results['voter_device_link_created']
            else:
                error_message = "Your account is not active, please contact the site admin."

            if user.id != voter_on_stage_id:
                # Eventually we want to merge voter_on_stage into user account
                pass
        else:
            error_message = "Your username and/or password were incorrect."
    elif not positive_value_exists(voter_on_stage_id):
        # If here, delete the prior voter_api_device_id from database
        voter_device_link_manager.delete_voter_device_link(voter_api_device_id)

        # We then need to set a voter_api_device_id cookie and create a new voter (even though not signed in)
        results = voter_setup(request)
        voter_api_device_id = results['voter_api_device_id']
        store_new_voter_api_device_id_in_cookie = results['store_new_voter_api_device_id_in_cookie']

    # Does Django think user is signed in?
    if request.user.is_authenticated():
        voter_signed_in = True
    else:
        info_message = "Please log in below..."

    if positive_value_exists(error_message):
        messages.add_message(request, messages.ERROR, error_message)
    if positive_value_exists(info_message):
        messages.add_message(request, messages.INFO, info_message)

    messages_on_stage = get_messages(request)
    template_values = {
        'request':              request,
        'username':             username,
        'next':                 next,
        'voter_signed_in':      voter_signed_in,
        'messages_on_stage':    messages_on_stage,
    }
    response = render(request, 'registration/login_user.html', template_values)

    # We want to store the voter_api_device_id cookie if it is new
    if positive_value_exists(voter_api_device_id) and positive_value_exists(store_new_voter_api_device_id_in_cookie):
        set_voter_api_device_id(request, response, voter_api_device_id)

    return response


def logout_user(request):
    logout(request)

    info_message = "You are now signed out."
    messages.add_message(request, messages.INFO, info_message)

    messages_on_stage = get_messages(request)
    template_values = {
        'request':              request,
        'next':                 '/admin/',
        'messages_on_stage':    messages_on_stage,
    }
    response = render(request, 'registration/login_user.html', template_values)

    # Find current voter_api_device_id
    voter_api_device_id = get_voter_api_device_id(request)

    delete_voter_api_device_id_cookie(response)

    # Now delete voter_api_device_id from database
    voter_device_link_manager = VoterDeviceLinkManager()
    voter_device_link_manager.delete_voter_device_link(voter_api_device_id)

    return response


def redirect_to_sign_in_page(request, authority_required={}):
    authority_required_text = ''
    for each_authority in authority_required:
        if each_authority == 'admin':
            authority_required_text += 'or ' if len(authority_required_text) > 0 else ''
            authority_required_text += 'has Admin rights'
        if each_authority == 'verified_volunteer':
            authority_required_text += 'or ' if len(authority_required_text) > 0 else ''
            authority_required_text += 'has Verified Volunteer rights'
    error_message = "You must sign in with account that " \
                    "{authority_required_text} to see that page." \
                    "".format(authority_required_text=authority_required_text)
    messages.add_message(request, messages.ERROR, error_message)

    if positive_value_exists(request.path):
        next_url_variable = '?next=' + request.path
    else:
        next_url_variable = ''
    return HttpResponseRedirect(LOGIN_URL + next_url_variable)


@login_required
def statistics_summary_view(request):
    authority_required = {'verified_volunteer'}  # admin, verified_volunteer
    if not voter_has_authority(request, authority_required):
        return redirect_to_sign_in_page(request, authority_required)

    google_civic_api_counter_manager = GoogleCivicApiCounterManager()
    google_civic_daily_summary_list = google_civic_api_counter_manager.retrieve_daily_summaries()
    vote_smart_api_counter_manager = VoteSmartApiCounterManager()
    vote_smart_daily_summary_list = vote_smart_api_counter_manager.retrieve_daily_summaries()
    template_values = {
        'google_civic_daily_summary_list':  google_civic_daily_summary_list,
        'vote_smart_daily_summary_list':    vote_smart_daily_summary_list,
    }
    response = render(request, 'admin_tools/statistics_summary.html', template_values)

    return response


@login_required
def sync_data_with_master_servers_view(request):
    authority_required = {'admin'}  # admin, verified_volunteer
    if not voter_has_authority(request, authority_required):
        return redirect_to_sign_in_page(request, authority_required)

    google_civic_election_id = request.GET.get('google_civic_election_id', '')
    state_code = request.GET.get('state_code', '')

    election_list = Election.objects.order_by('-election_day_text')

    state_list = STATE_CODE_MAP
    sorted_state_list = sorted(state_list.items())

    template_values = {
        'election_list':                election_list,
        'google_civic_election_id':     google_civic_election_id,
        'state_list':                   sorted_state_list,
        'state_code':                   state_code,

        'ballot_items_sync_url':        BALLOT_ITEMS_SYNC_URL,
        'ballot_returned_sync_url':     BALLOT_RETURNED_SYNC_URL,
        'candidates_sync_url':          CANDIDATES_SYNC_URL,
        'elections_sync_url':           ELECTIONS_SYNC_URL,
        'measures_sync_url':            MEASURES_SYNC_URL,
        'offices_sync_url':             OFFICES_SYNC_URL,
        'organizations_sync_url':       ORGANIZATIONS_SYNC_URL,
        'polling_locations_sync_url':   POLLING_LOCATIONS_SYNC_URL,
        'positions_sync_url':           POSITIONS_SYNC_URL,
        'voter_guides_sync_url':        VOTER_GUIDES_SYNC_URL,
    }
    response = render(request, 'admin_tools/sync_data_with_master_dashboard.html', template_values)

    return response
