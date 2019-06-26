let socket;
let freezeUpdatesUntil = -1;
let masterVersion = undefined;
let exportTableSettings = {};
let connStatusChangeTime = Date.now();
let onlineSlavesBackup;
let onlineSlavesDedupSorted;
let showOfflineClients = false;
let showOfflineSlaves = false;
let mostRecentHideClientTime = Date.now();
let mostRecentHideSlaveTime = Date.now();
let masterUptimeOnConnect = undefined;
let singleMachineFormVisible = true;
let singleMachineSelectedMachine = '';
let cpuTableVisible = false;
let clientsCheckboxChecked = false;
let slavesCheckboxChecked = false;
let isConfigurationLoaded = false;
let selectedPreset = undefined;

function freezeUpdates() {
  freezeUpdatesUntil = Date.now() + 15 * 1000;
}

function unfreezeUpdates() {
  freezeUpdatesUntil = -1;
}

function deleteConfig() {
  let ok = confirm('Are you sure you want to delete?');
  if (ok) {
    let element = document.getElementById('presetsSelect');
    let uid = element.options[element.selectedIndex].value;
    if (uid !== undefined && uid !== '') {
      let configs = JSON.parse(localStorage.getItem('presets'));
      delete configs[uid];
      localStorage.setItem('presets', JSON.stringify(configs));
    }
  }
  unfreezeUpdates();
  displaySavedPresets();
  socket.emit('get_info');
  return ok;
}

function connTimerUpdate() {
  let timerSpan = document.getElementById('masterStatusTimer');
  let currentDate = Date.now();
  let uptimeDays = Math.floor((currentDate - connStatusChangeTime) / 86400000);
  let daysText = ' DAYS ';
  if (uptimeDays == 1) daysText = ' DAY ';
  timerSpan.innerText = uptimeDays + daysText +
    new Date(currentDate - connStatusChangeTime).toISOString().substr(11, 8);
}

function toggleArrow(divName, transitionTime) {
  let arrowDiv = divName + 'Arrow';
  $('#' + divName).toggle(transitionTime);
  if ($('#' + arrowDiv)[0].innerHTML.includes('up')) {
    $('#' + arrowDiv)[0].innerHTML =
      '<img src="/static/images/arrow_down.png" />';
  } else {
    $('#' + arrowDiv)[0].innerHTML =
      '<img src="/static/images/arrow_up.png" />';
  }
}

function applyCoreChange(t) {
  let newCoreCount = document.getElementById('coreSelect').value;
  let element = document.getElementById('singleSelect');
  let uid = element.options[element.selectedIndex].value;
  if (uid != '') {
    socket.emit('change_core', [uid, parseInt(newCoreCount, 10)]);
  }
  unfreezeUpdates();
  t.disabled = 'true';

  function reactivate(t) {
    t.removeAttribute('disabled');
  }

  setTimeout(reactivate, 2000, t);
}

function updateExportTable(id, value) {
  exportTableSettings[id.substr(7)] = value;
  let selects = $("select[id|='export']");
  exportTableSettings = {};
  for (let select in selects) {
    let machineName = selects[select].id;
    let newValue = selects[select].value;
    if (machineName != undefined) {
      exportTableSettings[machineName.substr(7)] = newValue;
    }
  }
}

function hideOverlay() {
  document.getElementById('keyboardShortcutsGuide').style.visibility =
    'hidden';
}

function showOverlay() {
  document.getElementById('keyboardShortcutsGuide').style.visibility =
    'visible';
}

function stringifyCPUTable() {
  let modeName = escape(document.getElementById('batchExportName').value);
  let buffer = '{\n    "config_name": "' + modeName + '",\n';
  buffer += '    "machines": {\n';
  for (let prop in exportTableSettings) {
    buffer += '        "' + prop + '": ';
    buffer += exportTableSettings[prop];
    buffer += ',';
    buffer += '\n';
  }
  buffer = buffer.substr(0, buffer.length - 2) + '\n';
  buffer += '    }\n';
  buffer += '}';
  return [modeName, buffer];
}

function saveConfigToLocalStorage() {
  let modeNameBuffer = stringifyCPUTable();
  let modeName = modeNameBuffer[0];
  let buffer = modeNameBuffer[1];
  setPresetInLocalStorage(modeName, JSON.parse(buffer).machines);
  unfreezeUpdates();
  document.getElementById('batchExportMessages').innerHTML =
    'Configuration saved to local storage';
  socket.emit('get_info');
}


function exportFile() {
  let modeNameBuffer = stringifyCPUTable();
  let modeName = modeNameBuffer[0];
  let buffer = modeNameBuffer[1];
  let download = function (content, fileName, mimeType) {
    let a = document.createElement('a');
    if (navigator.msSaveBlob) { // IE10
      navigator.msSaveBlob(new Blob([content], {
        type: mimeType
      }), fileName);
    } else if (URL && 'download' in a) { //h5 a[download]
      a.href = URL.createObjectURL(new Blob([content], {
        type: mimeType
      }));
      a.setAttribute('download', fileName);
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } else {
      location.href = 'data:application/octet-stream,' +
        encodeURIComponent(content);
    }
  }
  download(buffer, modeName + '.json', 'application/json;encoding:utf-8');
  document.getElementById('batchExportMessages').innerHTML =
    'File download ready. Please check your downloaded files.';
}


function batchApplyCpuChanges(t) {
  for (let i in exportTableSettings) {
    let newUsedCores = parseInt(exportTableSettings[i], 10);
    for (let j in onlineSlavesBackup) {
      if (onlineSlavesBackup[j]['Hostname'] == i) {
        let currentUsedCores = parseInt(onlineSlavesBackup[j]['USED']);
        if (currentUsedCores != newUsedCores) {
          socket.emit('change_core',
            [
              onlineSlavesBackup[j]['UID'],
              parseInt(exportTableSettings[i], 10)
            ]
          );
        }
      }
    }
  }
  document.getElementById('applyBatchSettingsMessages').innerHTML =
    'Settings applied. Please wait and check table below for updates.';
  unfreezeUpdates();
  t.disabled = 'true';

  function reactivate(t) {
    t.removeAttribute('disabled');
  }

  setTimeout(reactivate, 2000, t);
}

function updateCoreSelector() {
  let element = document.getElementById('singleSelect');
  let uid = element.options[element.selectedIndex].value;
  singleMachineSelectedMachine = uid;
  let coreSelector = document.getElementById('coreSelect');
  coreSelector.innerHTML = '<option></option>';
  let matchingMachine = onlineSlavesBackup.filter(
    function (value, index, array) {
      return value['UID'] == uid;
    }
  );
  if (matchingMachine[0] === undefined) return;
  for (let i = 0; i <= parseInt(matchingMachine[0]['CORES']); i++) {
    let defaultSelected = false;
    let currentSelected = false;
    if (i == 1) defaultSelected = true;
    if (i == matchingMachine[0]['USED']) currentSelected = true;
    coreSelector.appendChild(
      new Option(i, i, defaultSelected, currentSelected)
    );
  }
  return true;
}

function toggleOnlineClientsDisplay() {
  let checked = document.getElementById('clientsCheckbox').checked;
  clientsCheckboxChecked = checked;
  document.getElementById('offlineClientEvents').innerHTML = '';
  showOfflineClients = !showOfflineClients;
  if (showOfflineClients) {
    mostRecentHideClientTime = undefined;
    document.getElementById('clientsCheckbox').checked = true;
    clientsCheckboxChecked = true;
  } else {
    mostRecentHideClientTime = Date.now();
    document.getElementById('clientsCheckbox').checked = false;
    clientsCheckboxChecked = false;
  }
  socket.emit('get_info'); // force refresh
}

function toggleOnlineSlavesDisplay() {
  let checked = document.getElementById('slavesCheckbox').checked;
  slavesCheckboxChecked = checked;
  document.getElementById('offlineSlaveEvents').innerHTML = '';
  showOfflineSlaves = !showOfflineSlaves;
  if (showOfflineSlaves) {
    mostRecentHideSlaveTime = undefined;
    document.getElementById('slavesCheckbox').checked = true;
    slavesCheckboxChecked = true;
  } else {
    mostRecentHideSlaveTime = Date.now();
    document.getElementById('slavesCheckbox').checked = false;
    slavesCheckboxChecked = false;
  }
  socket.emit('get_info'); // force refresh
}

function getPresetFromLocalStorage(configName) {
  if (localStorage.hasOwnProperty('presets')) {
    let presets = JSON.parse(localStorage.getItem('presets'));
    if (presets.hasOwnProperty(configName)) {
      return presets[configName];
    }
  }
  return null;
}

function setPresetInLocalStorage(configName, configData) {
  if (!localStorage.hasOwnProperty('presets')) {
    localStorage['presets'] = JSON.stringify({});
  }
  let presets = JSON.parse(localStorage.getItem('presets'));
  if (presets.hasOwnProperty(configName)) {
    let ok = confirm('You are about to overwrite an existing preset' +
      'configuration. Are you sure you want to continue?');
    if (!ok) return false;
  }
  presets[configName] = configData;
  localStorage.setItem('presets', JSON.stringify(presets));
  return true;
}

function createCpuEditingTable(dataSource) {
  if (dataSource === null) {
    dataSource = onlineSlavesDedupSorted;
    document.getElementById('importConfigMessages').innerHTML =
      'Configuration imported.';
  } else if ('options' in dataSource) {
    dataSource = getPresetFromLocalStorage(
      dataSource.options[dataSource.selectedIndex].value
    );
  }
  let batchForm = document.getElementById('batchExportForm');
  batchForm.innerHTML = '';
  let table = document.createElement('table');
  table.id = 'prepareExportTable';
  let tr = table.insertRow(-1);
  let th = document.createElement('th');      // TABLE HEADER.
  th.innerHTML = 'Hostname';
  tr.appendChild(th);
  th = document.createElement('th');      // TABLE HEADER.
  th.innerHTML = 'Core Count';
  tr.appendChild(th);
  batchForm.appendChild(table);
  for (let i in onlineSlavesDedupSorted) {
    let hostname = onlineSlavesDedupSorted[i]['Hostname'];
    let maxCores = onlineSlavesDedupSorted[i]['CORES'];
    let targetCores = onlineSlavesDedupSorted[i]['USED'];
    if (dataSource.hasOwnProperty(hostname)) {
      targetCores = dataSource[hostname];
    }
    tr = table.insertRow(-1);
    let tabCell = tr.insertCell(-1);
    tabCell.innerHTML = hostname;
    tabCell = tr.insertCell(-1);
    let thisSelect = document.createElement('select');
    thisSelect.id = 'export-' + hostname;
    exportTableSettings[hostname] = targetCores;
    thisSelect.innerHTML = '<option></option>';
    for (let j = 0; j <= maxCores; j++) {
      let defaultSelected = false;
      let currentSelected = false;
      if (j == targetCores) {
        defaultSelected = true;
        currentSelected = true;
      }
      thisSelect.appendChild(new Option(j, j,
        defaultSelected, currentSelected));
      tabCell.appendChild(thisSelect);
    }
  }
  $("select[id|='export']").change(function () {
    document.getElementById('applyBatchSettingsMessages').innerHTML = '';
    document.getElementById('batchExportMessages').innerHTML = '';
    updateExportTable(this.id, this.value);
  });
  $("select[id|='export']").focus(function () {
    freezeUpdates();
  });
  unfreezeUpdates();
  isConfigurationLoaded = true;
  if (!cpuTableVisible) {
    $('#batchExport').toggle(1);
    cpuTableVisible = true;
  }
}

function displaySavedPresets() {
  if (Date.now() > freezeUpdatesUntil) {
    if (localStorage.hasOwnProperty('presets')) {
      let presets = JSON.parse(localStorage.getItem('presets'));
      let newSelect = document.getElementById('presetsSelect');
      if (newSelect === null) {
        newSelect = document.createElement('select');
        newSelect.id = 'presetsSelect';
        document.getElementById('savedPresets').appendChild(newSelect);
      }
      newSelect.innerHTML = '<option></option>';
      for (let preset in presets) {
        if (preset === selectedPreset) {
          newSelect.appendChild(new Option(preset, preset, true, true));
        } else {
          newSelect.appendChild(new Option(preset, preset, false, false));
        }
      }
      $('#presetsSelect').focus(function () {
        freezeUpdates();
      });
      $('#presetsSelect').change(function () {
        selectedPreset = this.value;
        createCpuEditingTable(this);
      });
    }
  }
}

$(document).ready(function () {
  toggleArrow('batchProcess', 1);
  toggleArrow('singleMachine', 1);
  $('#batchExport').toggle(1);

  function f(e) {
    if (e.shiftKey) {
      if (e.keyCode == 49) { // Shift + 1
        toggleArrow('batchProcess', 300);
      } else if (e.keyCode == 50) { // Shift + 2
        toggleArrow('singleMachine', 300);
      } else if (e.keyCode == 51) { // Shift + 3
        toggleArrow('tables', 300);
      } else if (e.keyCode == 191) { // ? key
        let el = document.getElementById('keyboardShortcutsGuide');
        el.style.visibility =
          (el.style.visibility == 'visible') ? 'hidden' : 'visible';
      }
    } else if (e.keyCode == 27) { // Esc key
      hideOverlay();
    } else if (e.keyCode == 219) {// US keyboard [ key
      toggleOnlineClientsDisplay();
    } else if (e.keyCode == 221) {// US keyboard ] key
      toggleOnlineSlavesDisplay();
    }
  }

  $(document).bind('keydown', f);

  let namespace = '/admin';
  socket = io.connect('http://' + document.domain + ':' +
    location.port + namespace);

  socket.on('up_time_resp', function (e) {
    masterUptimeOnConnect = e;
    let newDateObj = new Date(Date.now() - e * 1000);
    connStatusChangeTime = newDateObj.getTime();
  });

  socket.on('connect', function () {
    console.log('Connected to Waldorf Master');
    let masterStatusSpan = document.getElementById('masterStatusSpan');
    masterStatusSpan.innerText = 'CONNECTED';
    masterStatusSpan.style = 'font-size: 16px; color:#222;' +
      'background-color:#9dd05e; padding: 5px 5px 0px 5px;';
    document.getElementById('masterStatusTimer').style = 'font-size: 16px;';
    connStatusChangeTime = Date.now();
    socket.emit('up_time');
  });

  socket.on('disconnect', function () {
    console.log('Disconnected from Master');
    masterVersion = undefined;
    let masterStatusSpan = document.getElementById('masterStatusSpan');
    masterStatusSpan.innerText = 'CONNECTION LOST';
    masterStatusSpan.style = 'font-size: 16px; background-color:#F58686; ' +
      'color:#222; padding: 5px 5px 0px 5px;';
    document.getElementById('masterStatusTimer').style = 'font-size: 16px;';
    connStatusChangeTime = Date.now()
  });

  socket.on('reconnect', function () {
    console.log('Reconnected with Master');
  });

  let fileInput = document.getElementById('fileInput');
  let fileDisplayArea = document.getElementById('fileDisplayArea');
  fileInput.addEventListener('change', function (e) {
    let file = fileInput.files[0];
    let textType = 'application/json';

    if (file.type == textType || file.type == '') {
      let reader = new FileReader();
      reader.onload = function (e) {
        let data = JSON.parse(reader.result);
        if (!(data.hasOwnProperty('machines') &&
          data.hasOwnProperty('config_name'))) {
          fileDisplayArea.innerText =
            'Invalid configuration file. Please try a different file.';
          return false;
        }
        fileDisplayArea.innerText = '';
        setPresetInLocalStorage(data.config_name, data.machines);
        createCpuEditingTable(data.machines);
      };
      reader.readAsText(file);
    } else {
      fileDisplayArea.innerText = 'File not supported!';
    }
  });

  socket.on('get_info_resp', function (msg) {
    masterVersion = msg.version;
    masterVersionSpan.innerHTML = 'v' + msg.version;

    displaySavedPresets();
    let msgObjectsClone = JSON.parse(JSON.stringify(msg.objects));
    let msgObjectsKeys = Object.keys(msgObjectsClone);
    let keysSorted = msgObjectsKeys.sort(function (a, b) {
      if (msgObjectsClone[a]['Hostname'] != msgObjectsClone[b]['Hostname']) {
        // alphabetical order, ascending
        return msgObjectsClone[a]['Hostname'] > msgObjectsClone[b]['Hostname'];
      } else {
        // most recent at the bottom
        return Date.parse(msgObjectsClone[a]['ConnectTime']) >
          Date.parse(msgObjectsClone[b]['ConnectTime']);
      }
    });

    let allMachines = [];
    for (let k in keysSorted) {
      allMachines.push(msgObjectsClone[keysSorted[k]]);
    }

    let clients = allMachines.filter(function (value, index, array) {
      return value['Type'] == 'Client';
    });

    let onlineClients = clients.filter(function (value, index, array) {
      return value['State'] == 'Online';
    });

    let slaves = allMachines.filter(function (value, index, array) {
      return value['Type'] == 'Slave';
    });

    let onlineSlaves = slaves.filter(function (value, index, array) {
      return value['State'] == 'Online';
    });

    onlineSlavesBackup = JSON.parse(JSON.stringify(onlineSlaves));

    let alreadySeen = [];
    let onlineSlavesCopyForSort = JSON.parse(JSON.stringify(onlineSlaves));
    onlineSlavesCopyForSort.sort(function (a, b) {
      return a['USED'] < b['USED']; // sort in reverse order
    });
    let onlineSlavesFiltered = [];
    for (let i in onlineSlavesCopyForSort) {
      if (!alreadySeen.includes(onlineSlavesCopyForSort[i]['Hostname'])) {
        alreadySeen.push(onlineSlavesCopyForSort[i]['Hostname']);
        onlineSlavesFiltered.push(onlineSlavesCopyForSort[i]);
      }
    }

    onlineSlavesDedupSorted = JSON.parse(JSON.stringify(onlineSlavesFiltered));
    let fileDisplayArea = document.getElementById('fileDisplayArea');
    if (onlineSlavesDedupSorted.length == 0) {
      fileDisplayArea.innerHTML =
        'No slaves connected. Please add machines to the ' +
        'Waldorf network first.';
      if (singleMachineFormVisible) {
        singleMachineFormVisible = false;
        $('#singleMachineForm').toggle(1);
        document.getElementById('singleMachineNotice').innerHTML =
          'There are no slaves connected';
        $('#importConfig').toggle(1);
        document.getElementById('fileDisplayArea').innerHTML =
          'There are no slaves connected';
      }
      if (cpuTableVisible) {
        cpuTableVisible = false;
        $('#batchExport').toggle(1);
      }
    } else {
      if (!singleMachineFormVisible) {
        singleMachineFormVisible = true;
        $('#singleMachineForm').toggle(1);
        document.getElementById('singleMachineNotice').innerHTML = '';
        $('#importConfig').toggle(1);
        document.getElementById('fileDisplayArea').innerHTML = '';
      }
      if (!cpuTableVisible) {
        if (isConfigurationLoaded) {
          $('#batchExport').toggle(1);
          cpuTableVisible = true;
        }
      }
    }

    let offlineClients = clients.filter(function (value, index, array) {
      return value['State'] != 'Online';
    });
    let newClientOfflineEvents = offlineClients.filter(
      function (value, index, array) {
        if (mostRecentHideClientTime !== undefined) {
          return Date.parse(value['DisconnectTime']) > mostRecentHideClientTime;
        }
        return false;
      }
    );

    newClientOfflineEvents.sort(function (a, b) {
      return a['DisconnectTime'] > b['DisconnectTime'];
    });

    if (newClientOfflineEvents.length > 0) {
      let clientEventsDiv = document.getElementById('offlineClientEvents');
      clientEventsDiv.innerHTML = 'New offline events (' +
        newClientOfflineEvents.length + '):<br /><br />';
      for (let h in newClientOfflineEvents) {
        clientEventsDiv.innerHTML += newClientOfflineEvents[h]['Hostname'] +
          ' (' + newClientOfflineEvents[h]['DisconnectTime'] + ')' + '<br />';
      }
      clientEventsDiv.innerHTML += '<br />Click to hide this information.' +
        '<br /><br />';
    }

    let offlineSlaves = slaves.filter(function (value, index, array) {
      return value['State'] != 'Online';
    });
    let newSlaveOfflineEvents = offlineSlaves.filter(
      function (value, index, array) {
        if (mostRecentHideSlaveTime !== undefined) {
          return Date.parse(value['DisconnectTime']) > mostRecentHideSlaveTime;
        }
        return false;
      }
    );
    newSlaveOfflineEvents.sort(function (a, b) {
      return a['DisconnectTime'] > b['DisconnectTime'];
    });

    if (newSlaveOfflineEvents.length > 0) {
      let slaveEventsDiv = document.getElementById('offlineSlaveEvents');
      slaveEventsDiv.innerHTML = 'New offline events (' +
        newSlaveOfflineEvents.length + '):<br /><br />';
      for (let h in newSlaveOfflineEvents) {
        slaveEventsDiv.innerHTML += newSlaveOfflineEvents[h]['Hostname'] +
          ' (' + newSlaveOfflineEvents[h]['DisconnectTime'] + ')' + '<br />';
      }
      slaveEventsDiv.innerHTML += '<br />Click to hide this information.' +
        '<br /><br />';
    }

    let totalCoreCount = onlineSlavesFiltered.reduce(function (a, b) {
      return parseInt(a) + parseInt(b['CORES']);
    }, 0);


    let usedCoreCount = onlineSlavesFiltered.reduce(function (a, b) {
      return parseInt(a) + parseInt(b['USED']);
    }, 0);

    let cpuUsageRate = 0;
    if (totalCoreCount > 0) {
      cpuUsageRate = Math.floor(100.0 * usedCoreCount / totalCoreCount);
    }
    document.getElementById('slaveCounter').innerHTML = '(' +
      onlineSlavesBackup.length + '): <br />Using ' + usedCoreCount + '/' +
      totalCoreCount + ' cores (' + cpuUsageRate + '%) on ' +
      onlineSlavesFiltered.length + ' host';
    if (onlineSlavesFiltered.length != 1) {
      document.getElementById('slaveCounter').innerHTML += 's';
    }

    let onlineSlavesInUse = onlineSlavesFiltered.filter(function (a) {
      return a['USED'] > 0;
    });
    let onlineSlavesLoadPer = 0.0;
    if (onlineSlavesInUse.length > 0) {
      onlineSlavesLoadPer = onlineSlavesInUse.reduce(function (a, b) {
        return a + parseFloat(b['LOAD(%)']);
      }, 0.0) / onlineSlavesInUse.length;
    }
    let onlineSlavesLoadTotal = 0.0;
    if (onlineSlavesFiltered.length > 0) {
      onlineSlavesLoadTotal = onlineSlavesFiltered.reduce(function (a, b) {
        return a + parseFloat(b['TOTAL(%)']);
      }, 0.0) / onlineSlavesFiltered.length;
    }

    document.getElementById('slaveCounter').innerHTML +=
      '<br />Average  Load: ' + onlineSlavesLoadPer.toFixed(1) + '%' +
      '  Total: ' + onlineSlavesLoadTotal.toFixed(1) + '%';

    document.getElementById('clientCounter').innerText = '(' +
      onlineClients.length + ')';

    if (slaves.length > 0) {
      if (!showOfflineSlaves) {
        if (onlineSlaves.length > 0) {
          createTableFromJson(msg.s_head, onlineSlaves, 'slaves');
        } else {
          let slavesHTML = 'Show offline slaves (press \']\' to toggle): ' +
            '<input type="checkbox" id="slavesCheckbox" ' +
            'onclick="toggleOnlineSlavesDisplay()" ';
          if (slavesCheckboxChecked) {
            slavesHTML += 'checked="checked"'
          }
          slavesHTML += '/><p>The slaves table can be sorted by choosing ' +
            'a column header</p><p>There are no clients currently connected</p>';
          document.getElementById('slavesDiv').innerHTML = slavesHTML;
        }
      } else {
        createTableFromJson(msg.s_head, slaves, 'slaves');
      }
    } else {
      let slavesHTML = 'Show offline slaves (press \']\' to toggle): ' +
        '<input type="checkbox" id="slavesCheckbox" ' +
        'onclick="toggleOnlineSlavesDisplay()" ';
      if (slavesCheckboxChecked) {
        slavesHTML += 'checked="checked"'
      }
      slavesHTML += '/><p>The slaves table can be sorted by choosing ' +
        'a column header</p><p>There are no clients currently connected</p>';
      document.getElementById('slavesDiv').innerHTML = slavesHTML;
    }
    if (clients.length > 0) {
      if (!showOfflineClients) {
        if (onlineClients.length > 0) {
          createTableFromJson(msg.c_head, onlineClients, 'clients');
        } else {
          let clientHTML = 'Show offline clients (press \'[\' to toggle): ' +
            '<input type="checkbox" id="clientsCheckbox" ' +
            'onclick="toggleOnlineClientsDisplay()" ';
          if (clientsCheckboxChecked) {
            clientHTML += ' checked="checked"'
          }
          clientHTML += '/><p>There are no clients currently connected</p>';
          document.getElementById('clientsDiv').innerHTML = clientHTML;
        }
      } else {
        createTableFromJson(msg.c_head, clients, 'clients');
      }
    } else {
      let clientHTML = 'Show offline clients (press \'[\' to toggle): ' +
        '<input type="checkbox" id="clientsCheckbox" ' +
        'onclick="toggleOnlineClientsDisplay()" ';
      if (clientsCheckboxChecked) {
        clientHTML += ' checked="checked"'
      }
      clientHTML += '/><p>There are no clients currently connected';
      document.getElementById('clientsDiv').innerHTML = clientHTML;
    }
  });

  socket.on('change_core_resp', function (msg) {
    console.log('Core change', msg);
  });

  setInterval(connTimerUpdate, 1000);

  function createTableFromJson(headers, msgData, tableDivName) {
    if (Date.now() > freezeUpdatesUntil) {
      if (tableDivName == 'slaves') {
        let selectObj = document.getElementById('singleSelect');
        selectObj.innerHTML = '<option></option>';
        let machines = [];
        let uids = [];
        for (let machine in onlineSlavesBackup) {
          machines.push(onlineSlavesBackup[machine]['Hostname']);
          uids.push(onlineSlavesBackup[machine]['UID']);
        }
        for (let i in machines) {
          if (uids[i] == singleMachineSelectedMachine) {
            selectObj.appendChild(new Option(machines[i] + ' (' +
              uids[i] + ')', uids[i], true, true));
          } else {
            selectObj.appendChild(new Option(machines[i] + ' (' +
              uids[i] + ')', uids[i], false, false));
          }
        }
      }
    }
    // Extract values for table header
    let col = [];
    for (let h in headers) {
      col.push(h);
    }
    // Create table dynamically
    let table = document.createElement('table');
    table.id = tableDivName + 'Table';
    // Create HTML table headers
    let tr = table.insertRow(-1); // table row
    for (let i = 0; i < col.length; i++) {
      let th = document.createElement('th'); // table header
      th.innerHTML = headers[col[i]];
      tr.appendChild(th);
    }
    // Add data from Master to the table
    for (let key in msgData) {
      let tr = table.insertRow(-1);
      let activeCoreCount = 0;
      let currentUid = '';
      for (let key2 in msgData[key]) {
        let tabCell = tr.insertCell(-1);
        if (key2 === 'State') {
          tabCell.style = 'color:#222;background-color:#9dd05e;';
          if (msgData[key][key2] != 'Online') {
            tabCell.style = 'color:#222;background-color:#F58686;';
          }
        }
        if (key2 == 'Version' && masterVersion != undefined) {
          tabCell.style = 'color:#222;background-color:#9dd05e;';
          if (msgData[key][key2] != masterVersion) {
            tabCell.style = 'color:#222;background-color:#F58686;';
          }
        }
        if (key2 == 'Ready') {
          tabCell.style = 'color:#222;background-color:#9dd05e;';
          if (msgData[key][key2] == 'False') {
            tabCell.style = 'color:#222;background-color:#F58686;';
          }
        }
        if (key2 === 'UID') {
          currentUid = msgData[key][key2];
        }
        if (key2 === 'CORES') {
          activeCoreCount = msgData[key][key2];
          tabCell.innerHTML = '';
          for (let k = 0; k < msgData[key]['USED']; k++) {
            tabCell.innerHTML += (1 + k) % 10 + ' ';
          }
          for (let k = msgData[key]['USED'];
               k < msgData[key][key2]; k++) {
            tabCell.innerHTML += '- ';
          }
        } else {
          tabCell.innerHTML = msgData[key][key2];
        }
      }
    }
    // Append newly created table to HTML div
    let divContainer = document.getElementById(tableDivName + 'Div');
    let headRowClassName = [];
    if (tableDivName == 'slaves' && divContainer.childNodes.length >= 3) {
      let existingTable = divContainer.childNodes[3];
      if (existingTable.tHead !== undefined) {
        headrow = existingTable.tHead.rows[0].cells;
        for (let i = 0; i < headrow.length; i++) {
          if (headrow[i].className.search(/sorttable_sorted_reverse/) != -1) {
            headRowClassName = [i, headrow[i].className, 0];
          } else if (headrow[i].className.search(/sorttable_sorted/) != -1) {
            headRowClassName = [i, headrow[i].className, 1];
          }
        }
      }
    }

    if (tableDivName != 'slaves') {
      let clientHTML = 'Show offline clients (press \'[\' to toggle): ' +
        '<input type="checkbox" id="clientsCheckbox" ' +
        'onclick="toggleOnlineClientsDisplay()" ';
      if (clientsCheckboxChecked) {
        clientHTML += ' checked="checked"'
      }
      clientHTML += '/>';
      divContainer.innerHTML = clientHTML;
      divContainer.appendChild(table);
      return false;
    }

    sorttable.makeSortable(table);
    table.className = 'sortable';
    if (headRowClassName.length > 0) {
      headrow = table.tHead.rows[0].cells;
      for (let i = 0; i < headrow.length; i++) {
        if (i == headRowClassName[0]) {
          headrow[i].id = tableDivName + 'Marked';
        } else {
          delete headrow[i].id;
          delete headrow[i].className;
        }
      }
    }

    let slavesHTML = 'Show offline slaves (press \']\' to toggle): ' +
      '<input type="checkbox" id="slavesCheckbox" ' +
      'onclick="toggleOnlineSlavesDisplay()" ';
    if (slavesCheckboxChecked) {
      slavesHTML += 'checked="checked"'
    }
    slavesHTML += '/><p>The slaves table can be sorted by choosing ' +
      'a column header</p>';
    divContainer.innerHTML = slavesHTML;
    divContainer.appendChild(table);

    if (headRowClassName.length > 0) {
      sorttable.innerSortFunction.apply(
        document.getElementById(tableDivName + 'Marked'), []
      );
      if (headRowClassName[2] == 0) {
        let temp = document.getElementById(tableDivName + 'Table').tBodies[0];
        sorttable.reverse(temp);
        document.getElementById(tableDivName + 'Table').tBodies[0] = temp;
        that = document.getElementById(tableDivName + 'Marked');
        that.className = that.className.replace('sorttable_sorted',
          'sorttable_sorted_reverse');
        that.removeChild(document.getElementById('sorttable_sortfwdind'));
        sortrevind = document.createElement('span');
        sortrevind.id = 'sorttable_sortrevind';
        sortrevind.innerHTML = stIsIE ?
          '&nbsp<font face="webdings">5</font>' : '&nbsp;&#x25B4;';
        that.appendChild(sortrevind);
      }
    }
  }
});
