from validation.publishing.publish import commit_title


def test_titles_lead_with_the_data_source():
    assert commit_title('2026-10-11-scaleswe-3f2a1b4c5d6e') == 'scaleswe: validation run 2026-10-11-scaleswe-3f2a1b4c5d6e'
    assert commit_title('2026-10-11-tasktrove-bugsinpy-3f2a1b4c5d6e', 'environment images for') == (
        'tasktrove-bugsinpy: environment images for 2026-10-11-tasktrove-bugsinpy-3f2a1b4c5d6e')


def test_unrecognized_run_ids_keep_the_plain_title():
    assert commit_title('custom-run') == 'Validation run custom-run'


def test_folder_catch_all_maps_every_repository_task_to_one_data_source():
    from validation.publishing.publish import folder_of
    mapping = {'*': 'swelego-real-data-verified', 'crosscodeeval-java': 'crosscodeeval-java-v4'}
    assert folder_of('dask__dask-1150', mapping) == 'swelego-real-data-verified'
    assert folder_of('crosscodeeval-java-17', mapping) == 'crosscodeeval-java-v4'
    assert folder_of('dask__dask-1150', {}) == 'dask__dask'
